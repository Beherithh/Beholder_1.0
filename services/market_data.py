import asyncio
import ccxt.async_support as ccxt
from datetime import datetime, timedelta
from typing import List, Dict
from loguru import logger
from sqlmodel import select, desc
from database.models import MonitoredPair, MarketData
from sqlalchemy.ext.asyncio import AsyncSession
from services.alert_engine import AlertEngine

class MarketDataService:
    """
    Сервис для загрузки рыночных данных (свечей) через CCXT.
    """
    
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.alert_engine = AlertEngine(session_factory)
    
    async def _get_last_candle_time(self, session: AsyncSession, pair_id: int) -> datetime:
        """
        Получает время последней свечи для пары.
        Если свечей нет, возвращает время 30 дней назад.
        """
        statement = select(MarketData).where(MarketData.pair_id == pair_id).order_by(desc(MarketData.timestamp)).limit(1)
        result = await session.execute(statement)
        last_candle = result.scalars().first()
        
        if last_candle:
            return last_candle.timestamp
        else:
            # Если истории нет, начинаем с 30 дней назад
            return datetime.utcnow() - timedelta(days=30)

    async def update_pair_history(self, session: AsyncSession, exchange, pair: MonitoredPair) -> int:
        """
        Обновляет историю для одной пары, используя переданный экземпляр биржи.
        В цикле загружает все доступные свечи до текущего момента.
        Возвращает общее количество добавленных свечей.
        """
        total_new_candles = 0
        try:
            last_time = await self._get_last_candle_time(session, pair.id)
            logger.info(f"[{pair.exchange}] Начинаем догрузку {pair.symbol} с {last_time}...")

            while True:
                # CCXT требует timestamp в миллисекундах
                since = int(last_time.timestamp() * 1000)
                
                candles = await exchange.fetch_ohlcv(pair.symbol, timeframe='1h', since=since)
                
                if not candles:
                    # Если биржа вернула пустой список, значит, мы все скачали
                    break

                new_count_in_batch = 0
                
                for candle in candles:
                    ts_ms, o, h, l, c, v = candle
                    candle_time = datetime.fromtimestamp(ts_ms / 1000)
                    
                    # Защита от дублей: если свеча совпадает с last_time, пропускаем
                    if candle_time <= last_time:
                        continue
                        
                    market_data = MarketData(
                        pair_id=pair.id,
                        timestamp=candle_time,
                        open=o, high=h, low=l, close=c, volume=v
                    )
                    session.add(market_data)
                    new_count_in_batch += 1
                
                if new_count_in_batch == 0:
                    # Если мы обработали пачку, но не нашли ни одной новой свечи
                    # (например, все были дубликатами), выходим из цикла.
                    break

                total_new_candles += new_count_in_batch
                
                # Обновляем last_time для следующей итерации
                last_candle_in_batch_ts = candles[-1][0]
                last_time = datetime.fromtimestamp(last_candle_in_batch_ts / 1000)
                
                logger.info(f"[{pair.exchange}] ...загружено {new_count_in_batch} свечей, последняя: {last_time}")

                # Сохраняем пачку в БД
                await session.commit()
                
                # Небольшая задержка между запросами, чтобы не получить бан
                await asyncio.sleep(exchange.rateLimit / 1000.0)

            if total_new_candles > 0:
                logger.success(f"[{pair.exchange}] Всего сохранено {total_new_candles} новых свечей для {pair.symbol}")
            else:
                logger.info(f"[{pair.exchange}] Нет новых свечей для {pair.symbol}")
                
            return total_new_candles

        except Exception as e:
            logger.error(f"Ошибка при обновлении {pair.exchange}:{pair.symbol} -> {e}")
            return 0

    async def update_all(self):
        """
        Обновляет данные OHLCv для ВСЕХ активных пар.
        Группирует пары по бирже, чтобы переиспользовать соединение и соблюдать rateLimit.
        """
        async with self.session_factory() as session:
            # Получаем все активные пары
            result = await session.execute(select(MonitoredPair).where(MonitoredPair.monitoring_status == "active"))
            pairs = result.scalars().all()
        
        if not pairs:
            logger.info("Нет активных пар для обновления.")
            return

        # Группировка по бирже
        pairs_by_exchange: Dict[str, List[MonitoredPair]] = {}
        for pair in pairs:
            ex_name = pair.exchange.lower()
            if ex_name not in pairs_by_exchange:
                pairs_by_exchange[ex_name] = []
            pairs_by_exchange[ex_name].append(pair)
            
        logger.info(f"Начинаем обновление для {len(pairs_by_exchange)} бирж...")

        for ex_name, exchange_pairs in pairs_by_exchange.items():
            if ex_name not in ccxt.exchanges:
                logger.warning(f"Биржа '{ex_name}' не поддерживается CCXT. Пропускаем.")
                continue
                
            try:
                # Инициализируем биржу один раз для всей пачки
                exchange_class = getattr(ccxt, ex_name)
                async with exchange_class() as exchange:
                    # Включаем встроенный rate limiter в CCXT (если есть)
                    exchange.enableRateLimit = True 
                    
                    logger.info(f"Запуск сессии для {ex_name.upper()} (Пар: {len(exchange_pairs)})")
                    
                    for pair in exchange_pairs:
                        # Открываем новую сессию БД для каждой пары
                        async with self.session_factory() as session:
                            await self.update_pair_history(session, exchange, pair)
                        
                        # Дополнительная задержка между обработкой пар
                        await asyncio.sleep(1)
                        
            except Exception as e:
                logger.error(f"Критическая ошибка при работе с биржей {ex_name}: {e}")
            
        logger.info("Обновление всех пар завершено.")
        
        # После обновления запускаем анализ
        await self.analyze_all_pairs_math_alerts()

    async def analyze_all_pairs_math_alerts(self):
        """
        Проверка условий алертов изменения цен и объёма для всех активных пар.
        """
        logger.info("Запуск анализа рыночных данных на алерты...")
        
        from services.system import get_config_service
        config = await get_config_service().get_alert_config()

        async with self.session_factory() as session:
            # 2. Получаем все активные пары
            result = await session.execute(select(MonitoredPair).where(MonitoredPair.monitoring_status == "active"))
            pairs = result.scalars().all()

            # 3. Собираем курсы Quote -> USDT
            rates = await self._get_quote_rates(pairs)

            for pair in pairs:
                # Делегируем анализ AlertEngine
                await self.alert_engine.analyze_pair(session, pair, config, rates)

    async def _get_quote_rates(self, pairs: List[MonitoredPair]) -> Dict[str, float]:
        """
        Получает текущие курсы Quote -> USDT для всех активных пар через CCXT.
        """
        quotes = set()
        for p in pairs:
            if '/' in p.symbol:
                _, quote = p.symbol.split('/')
                if quote != 'USDT':
                    quotes.add(quote)
        
        rates = {'USDT': 1.0}
        if not quotes:
            return rates
            
        logger.info(f"Сбор курсов для валют: {quotes}")
        
        # Для простоты используем gateio как основной источник курсов
        try:
            async with ccxt.gateio() as ex:
                for q in quotes:
                    try:
                        # Пытаемся получить курс к USDT
                        symbol = f"{q}/USDT"
                        ticker = await ex.fetch_ticker(symbol)
                        rates[q] = float(ticker['last'])
                        logger.info(f"Актуальный курс {q}/USDT: {rates[q]}")
                    except Exception as e:
                        logger.warning(f"Не удалось получить курс {q}/USDT: {e}")
                        rates[q] = 1.0 # Пропускаем конвертацию если не вышло
        except Exception as e:
            logger.error(f"Критическая ошибка при получении курсов валют: {e}")
            
        return rates
