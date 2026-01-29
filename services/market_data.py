import asyncio
import ccxt.async_support as ccxt
from datetime import datetime, timedelta
from typing import List, Dict
from loguru import logger
from sqlmodel import select, desc, func
from database.models import MonitoredPair, MarketData, AppSettings, Signal, SignalType
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
        
        # После обновления запускаем анализ
        await self.analyze_all_pairs_math_alerts()

    async def analyze_all_pairs_math_alerts(self):
        """
        Проверка условий алертов изменения цен и объёма для всех активных пар.
        """
        logger.info("Запуск анализа рыночных данных на алерты...")
        async with self.session_factory() as session:
            # 1. Загружаем настройки алертов
            async def get_val_int(key):
                s = await session.get(AppSettings, key)
                try: 
                    return int(float(s.value)) if s and s.value and s.value != 'None' else None
                except: return None

            async def get_val_float(key):
                s = await session.get(AppSettings, key)
                try:
                    return float(s.value) if s and s.value and s.value != 'None' else None
                except: return None

            # Fallback для старых ключей если новые не заданы (хотя UI уже должен был мигрировать)
            # Но здесь читаем напрямую из БД, так что полезно иметь фоллбек
            old_h_period = await get_val_int("alert_price_hours_period")
            old_d_period = await get_val_int("alert_price_days_period")

            config = {
                "h_pump_period": await get_val_int("alert_price_hours_pump_period") or old_h_period,
                "h_dump_period": await get_val_int("alert_price_hours_dump_period") or old_h_period,
                "h_pump_threshold": await get_val_float("alert_price_hours_pump_threshold"),
                "h_dump_threshold": await get_val_float("alert_price_hours_dump_threshold"),
                
                "d_pump_period": await get_val_int("alert_price_days_pump_period") or old_d_period,
                "d_dump_period": await get_val_int("alert_price_days_dump_period") or old_d_period,
                "d_pump_threshold": await get_val_float("alert_price_days_pump_threshold"),
                "d_dump_threshold": await get_val_float("alert_price_days_dump_threshold"),
                
                "v_period": await get_val_int("alert_volume_days_period"),
                "v_threshold": await get_val_float("alert_volume_days_threshold"),
            }

            # 2. Получаем все активные пары
            result = await session.execute(select(MonitoredPair).where(MonitoredPair.monitoring_status == "active"))
            pairs = result.scalars().all()

            # 3. Собираем курсы Quote -> USDT
            rates = await self._get_quote_rates(pairs)

            for pair in pairs:
                await self._check_price_vol_alerts(session, pair, config, rates)

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

    async def _check_price_vol_alerts(self, session: AsyncSession, pair: MonitoredPair, config: dict, rates: dict):
        """Проверка алертов изменения цен и объёма для конкретной пары"""
        now = datetime.utcnow()

        # --- Алерты по цене (Часы и Дни) ---
        # Теперь 4 отдельных проверки
        checks = [
            ("hours", "pump", config["h_pump_period"], config["h_pump_threshold"]),
            ("hours", "dump", config["h_dump_period"], config["h_dump_threshold"]),
            ("days", "pump", config["d_pump_period"], config["d_pump_threshold"]),
            ("days", "dump", config["d_dump_period"], config["d_dump_threshold"]),
        ]

        for period_type, direction_type, period_val, threshold in checks:
            if period_val is None or threshold is None or threshold <= 0:
                continue
            
            delta = timedelta(hours=period_val) if period_type == "hours" else timedelta(days=period_val)
            since_time = now - delta
            
            # Берем все свечи за период
            stmt = select(MarketData).where(
                MarketData.pair_id == pair.id,
                MarketData.timestamp >= since_time
            ).order_by(MarketData.timestamp.asc())
            candles = (await session.execute(stmt)).scalars().all()
            
            if len(candles) < 2: continue
            
            # Ищем Min и Max
            min_candle = min(candles, key=lambda x: x.low)
            max_candle = max(candles, key=lambda x: x.high)
            
            p_min = min_candle.low
            p_max = max_candle.high
            
            if p_max == 0 or p_min == 0: continue
            
            change = 0.0
            alert_msg = ""
            
            if direction_type == "dump":
                # Для дампа нас интересует падение от Максимума до (текущего или минимума).
                # Алгоритм "Max -> Min" (если Max был раньше Min) или просто Drop from High?
                # Старая логика: if max_candle.timestamp < min_candle.timestamp
                # Но теперь у нас разный период.
                # Давайте сделаем проще и надежнее: Drop from Max in period.
                # Находим Max. Смотрим минимальную цену ПОСЛЕ Max.
                
                # Вариант "как было":
                if max_candle.timestamp < min_candle.timestamp:
                     change = (p_min / p_max - 1) * 100
                     if abs(change) >= threshold:
                        alert_msg = f"📉 DUMP {pair.symbol} \n" \
                                    f"({pair.exchange}): {pair.source_label}\n" \
                                    f"{change:+.2f}% за {period_val} {period_type}\n" \
                                    f"Min: {p_min} | Max: {p_max}"
            
            elif direction_type == "pump":
                # Для пампа: Min -> Max (если Min был раньше Max)
                 if min_candle.timestamp < max_candle.timestamp:
                     change = (p_max / p_min - 1) * 100
                     if change >= threshold:
                        alert_msg = f"📈 PUMP {pair.symbol} \n" \
                                    f"({pair.exchange}): {pair.source_label}\n" \
                                    f"{change:+.2f}% за {period_val} {period_type}\n" \
                                    f"Min: {p_min} | Max: {p_max}"

            if alert_msg:
                await self._create_signal_if_new(session, SignalType.PRICE_CHANGE, alert_msg)

        # --- Алерт по объему (Дни) ---
        v_period = config["v_period"]
        v_threshold = config["v_threshold"]
        if v_period and v_threshold > 0:
            since_v = now - timedelta(days=v_period)
            stmt_v = select(func.sum(MarketData.volume * MarketData.close)).where(
                MarketData.pair_id == pair.id,
                MarketData.timestamp >= since_v
            )
            total_v_raw = (await session.execute(stmt_v)).scalar() or 0.0
            
            # Конвертируем в USDT если нужно
            quote = pair.symbol.split('/')[1] if '/' in pair.symbol else "USDT"
            rate = rates.get(quote, 1.0)
            total_v_usdt = total_v_raw * rate

            if total_v_usdt <= v_threshold * v_period: # Умножаем порог на период, костыль?
                v_msg = f"📊 LOW VOLUME {pair.symbol}\n" \
                        f"({pair.exchange}): {pair.source_label}\n" \
                        f"Объем за {v_period} дн: {total_v_usdt:,.0f} USDT"
                
                if rate != 1.0:
                    v_msg += f" (курс {quote}: {rate})"
                
                v_msg += f"\nПорог: {v_threshold:,.0f} USDT"
                await self._create_signal_if_new(session, SignalType.VOLUME_ALERT, v_msg)

    async def _create_signal_if_new(self, session: AsyncSession, sig_type: SignalType, msg: str):
        """Создает сигнал в БД и отправляет в ТГ, если такого сообщения еще не было за последние N часов"""
        # Получаем настройку окна дедупликации
        dedup_setting = await session.get(AppSettings, "alert_dedup_hours")
        try:
            dedup_hours = int(float(dedup_setting.value)) if dedup_setting and dedup_setting.value else 12
        except:
            dedup_hours = 12
        
        # Проверяем дубликат за последние N часов
        cutoff_time = datetime.utcnow() - timedelta(hours=dedup_hours)
        stmt = select(Signal).where(
            Signal.type == sig_type,
            Signal.raw_message == msg,
            Signal.created_at >= cutoff_time
        )
        existing = (await session.execute(stmt)).first()
        
        if not existing:
            new_sig = Signal(type=sig_type, raw_message=msg)
            session.add(new_sig)
            await session.commit()
            await session.refresh(new_sig)
            
            logger.warning(f"NEW ANALYSIS SIGNAL: {msg}")
            
            from services.notifications import send_and_log_signal
            # Используем create_task чтобы не блокировать анализ
            asyncio.create_task(send_and_log_signal(new_sig.id, msg, prefix="[ANALYSIS]"))
