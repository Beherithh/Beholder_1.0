import asyncio
import ccxt.async_support as ccxt
from datetime import datetime, timedelta
from typing import List, Dict
from loguru import logger
from sqlmodel import select, desc

from database.models import MonitoredPair, MarketData
from sqlalchemy.ext.asyncio import AsyncSession

class MarketDataService:
    """
    Сервис для загрузки рыночных данных (свечей) через CCXT.
    Использует асинхронность для предотвращения зависания интерфейса во время сетевых запросов.
    """
    
    def __init__(self, session_factory):
        self.session_factory = session_factory
    
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
        Возвращает количество добавленных свечей.
        """
        try:
            # 1. Вычисляем "since" (с какого момента качать)
            last_time = await self._get_last_candle_time(session, pair.id)
            # CCXT требует timestamp в миллисекундах
            since = int(last_time.timestamp() * 1000)
            
            logger.info(f"[{pair.exchange}] Скачиваем {pair.symbol} с {last_time}...")
            
            # 2. Скачиваем свечи (Timeframe 1h)
            candles = await exchange.fetch_ohlcv(pair.symbol, timeframe='1h', since=since)
            
            new_count = 0
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
                new_count += 1
            
            if new_count > 0:
                await session.commit()
                logger.success(f"[{pair.exchange}] Сохранено {new_count} новых свечей для {pair.symbol}")
            else:
                logger.info(f"[{pair.exchange}] Нет новых свечей для {pair.symbol}")
                
            return new_count

        except Exception as e:
            logger.error(f"Ошибка при обновлении {pair.exchange}:{pair.symbol} -> {e}")
            return 0

    async def update_all(self):
        """
        Обновляет данные для ВСЕХ активных пар.
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
                        # Открываем новую сессию БД для каждой пары (или можно одну на всех, но так безопаснее для транзакций)
                        async with self.session_factory() as session:
                            await self.update_pair_history(session, exchange, pair)
                        
                        # Ручная задержка на основе rateLimit, если enableRateLimit вдруг не отработал как ожидалось
                        # exchange.rateLimit в миллисекундах
                        wait_ms = exchange.rateLimit
                        await asyncio.sleep(wait_ms / 1000.0)
                        
            except Exception as e:
                logger.error(f"Критическая ошибка при работе с биржей {ex_name}: {e}")
            
        logger.info("Обновление всех пар завершено.")
