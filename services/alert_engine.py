import asyncio
from datetime import datetime, timedelta, timezone
from loguru import logger
from sqlmodel import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MonitoredPair, MarketData, Signal, SignalType
from services.config import AlertConfig

class AlertEngine:
    """
    Сервис для анализа рыночных данных и генерации сигналов (Pump/Dump, Volume).
    Не занимается загрузкой данных, только анализом уже сохраненных свечей.
    """

    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def analyze_pair(self, session: AsyncSession, pair: MonitoredPair, config: AlertConfig, rates: dict):
        """
        Основной метод анализа одной пары.
        """
        await self._check_price_alerts(session, pair, config)
        await self._check_volume_alerts(session, pair, config, rates)

    async def _check_price_alerts(self, session: AsyncSession, pair: MonitoredPair, config: AlertConfig):
        """Проверка алертов изменения цены (Pump/Dump)"""
        now = datetime.now(timezone.utc)
        
        checks = [
            ("hours", "pump", config.h_pump_period, config.h_pump_threshold),
            ("hours", "dump", config.h_dump_period, config.h_dump_threshold),
            ("days", "pump", config.d_pump_period, config.d_pump_threshold),
            ("days", "dump", config.d_dump_period, config.d_dump_threshold),
        ]

        # Собираем шаблоны активных конфигураций для очистки "сирот" одним запросом
        active_patterns = set()

        for period_type, direction_type, period_val, threshold in checks:
            if period_val is None or threshold is None or threshold <= 0:
                continue
            
            period_str = f"in {period_val} {period_type}"
            direction_tag = "PUMP" if direction_type == "pump" else "DUMP"
            
            # Добавляем паттерн, который мы будем "защищать" от удаления
            # Например: "%PUMP%in 6 hours%"
            active_patterns.add(f"%{direction_tag}%{period_str}%")

            delta = timedelta(hours=period_val) if period_type == "hours" else timedelta(days=period_val)
            since_time = now - delta
            
            # Берем все свечи за период
            stmt = select(MarketData).where(
                MarketData.pair_id == pair.id,
                MarketData.timestamp >= since_time
            ).order_by(MarketData.timestamp.asc())
            candles = (await session.execute(stmt)).scalars().all()
            
            is_triggered = False
            alert_msg = ""
            
            if len(candles) >= 2:
                # Ищем Min и Max
                min_candle = min(candles, key=lambda x: x.low)
                max_candle = max(candles, key=lambda x: x.high)
                
                # Нормализуем timestamp для корректного сравнения
                min_time = min_candle.timestamp
                max_time = max_candle.timestamp
                if min_time.tzinfo is None:
                    min_time = min_time.replace(tzinfo=timezone.utc)
                if max_time.tzinfo is None:
                    max_time = max_time.replace(tzinfo=timezone.utc)
                
                p_min = min_candle.low
                p_max = max_candle.high
                
                if p_max > 0 and p_min > 0:
                    change = 0.0
                    
                    if direction_type == "dump":
                        if max_time < min_time:
                             change = (p_min / p_max - 1) * 100
                             if abs(change) >= threshold:
                                is_triggered = True
                                alert_msg = f"📉 DUMP <b>{pair.symbol}</b> \n" \
                                            f"({pair.exchange}): {pair.source_label}\n" \
                                            f"<b>{change:+.0f}%</b> {period_str}\n" \
                                            f"Min: {p_min} | Max: {p_max}"
                    
                    elif direction_type == "pump":
                         if min_time < max_time:
                             change = (p_max / p_min - 1) * 100
                             if change >= threshold:
                                is_triggered = True
                                alert_msg = f"📈 PUMP <b>{pair.symbol}</b> \n" \
                                            f"({pair.exchange}): {pair.source_label}\n" \
                                            f"<b>{change:+.0f}%</b> {period_str}\n" \
                                            f"Min: {p_min} | Max: {p_max}"

            if is_triggered:
                await self._create_signal_if_new(session, SignalType.PRICE_CHANGE, alert_msg, pair.id, unique_filter=period_str)
            else:
                # Если условие перестало выполняться для ЭТОГО конкретного периода и направления - удаляем
                await session.execute(delete(Signal).where(
                    Signal.pair_id == pair.id, 
                    Signal.type == SignalType.PRICE_CHANGE,
                    Signal.raw_message.like(f"%{direction_tag}%"),
                    Signal.raw_message.like(f"%{period_str}%")
                ))
                await session.commit()
        
        # Очистка "осиротевших" алертов (Config Change Cleanup)
        # Удаляем все сигналы PUMP/DUMP для этой пары, которые НЕ соответствуют ни одной из активных конфигураций.
        # Это обрабатывает случаи:
        # 1. Смена периода (6h -> 7h): старый "in 6 hours" удалится.
        # 2. Отключение типа (Pump выкл): старый "PUMP" удалится.
        
        cleanup_stmt = delete(Signal).where(
            Signal.pair_id == pair.id,
            Signal.type == SignalType.PRICE_CHANGE
        )
        
        for pattern in active_patterns:
            cleanup_stmt = cleanup_stmt.where(Signal.raw_message.notlike(pattern))
            
        await session.execute(cleanup_stmt)
        await session.commit()

    async def _check_volume_alerts(self, session: AsyncSession, pair: MonitoredPair, config: AlertConfig, rates: dict):
        """Проверка алертов по объему"""
        v_period = config.v_period
        v_threshold = config.v_threshold
        
        if not (v_period and v_threshold and v_threshold > 0):
            return

        now = datetime.now(timezone.utc)
        since_v = now - timedelta(days=v_period)
        
        stmt_v = select(func.sum(MarketData.volume * MarketData.close)).where(
            MarketData.pair_id == pair.id,
            MarketData.timestamp >= since_v
        )
        total_v_raw = (await session.execute(stmt_v)).scalar() or 0.0
        
        # Конвертируем в USDT если нужно
        quote = pair.symbol.split('/')[1] if '/' in pair.symbol else "USDT"
        
        rate = rates.get(quote) if quote != "USDT" else 1.0
        
        if rate is None:
            logger.warning(f"Пропуск расчета объема для {pair.symbol}: курс {quote}/USDT недоступен.")
            return

        total_v_usdt = total_v_raw * rate

        if total_v_usdt <= v_threshold * v_period:
            v_msg = f"📊 Low Volume <b>{pair.symbol}</b>\n" \
                    f"({pair.exchange}): {pair.source_label}\n" \
                    f"Volume in {v_period} days: {total_v_usdt:,.0f} USDT\n" \
                    f"<b>{total_v_usdt/v_period:,.0f}</b> USDT/day\n"    
            
            if rate != 1.0:
                v_msg += f" (quote {quote}: {rate})"
            
            v_msg += f"\nThreshold: {v_threshold:,.0f} USDT"
            await self._create_signal_if_new(session, SignalType.VOLUME_ALERT, v_msg, pair.id)
        else:
            # Объем в норме -> удаляем старые алерты по объему
            await session.execute(delete(Signal).where(
                Signal.pair_id == pair.id,
                Signal.type == SignalType.VOLUME_ALERT
            ))
            await session.commit()

    async def _create_signal_if_new(self, session: AsyncSession, sig_type: SignalType, msg: str, pair_id: int | None = None, unique_filter: str | None = None):
        """Создает сигнал в БД и отправляет в ТГ, если такого сообщения еще нет в активном пуле"""
        if pair_id is None:
            logger.error(f"Attempted to create signal with pair_id=None! Msg: {msg}")
            return

        # Базовое условие: тот же тип, та же пара
        conditions = [
            Signal.type == sig_type,
            Signal.pair_id == pair_id,
        ]
        
        # Для изменения цены проверяем направление (PUMP или DUMP)
        if sig_type == SignalType.PRICE_CHANGE:
            if "PUMP" in msg:
                conditions.append(Signal.raw_message.like("%PUMP%"))
            elif "DUMP" in msg:
                conditions.append(Signal.raw_message.like("%DUMP%"))
            
            # Уточняем поиск по периоду (чтобы 30d не блокировался 6h)
            if unique_filter:
                conditions.append(Signal.raw_message.like(f"%{unique_filter}%"))
        # Для других типов (например VOLUME_ALERT) достаточно совпадения типа и pair_id

        stmt = select(Signal).where(*conditions)
        existing = (await session.execute(stmt)).first()
        
        if not existing:
            try:
                new_sig = Signal(type=sig_type, pair_id=pair_id, raw_message=msg)
                session.add(new_sig)
                await session.commit()
                await session.refresh(new_sig)
                
                logger.warning(f"NEW ANALYSIS SIGNAL: {msg}")
                
                # Импорт внутри метода во избежание циклической зависимости
                from services.notifications import send_and_log_signal
                asyncio.create_task(send_and_log_signal(new_sig.id, msg, prefix=""))
            except Exception as e:
                logger.error(f"Failed to save signal to DB: {e}")
                await session.rollback()
        else:
            # Логируем, что сигнал уже существует, чтобы было понятно, почему не создается новый
            logger.info(f"Signal already exists (ID: {existing[0].id}), skipping creation. Msg: {msg[:50]}...")