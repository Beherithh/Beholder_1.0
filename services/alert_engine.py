import asyncio
from datetime import datetime, timedelta
from loguru import logger
from sqlmodel import select, func
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
        now = datetime.utcnow()
        
        checks = [
            ("hours", "pump", config.h_pump_period, config.h_pump_threshold),
            ("hours", "dump", config.h_dump_period, config.h_dump_threshold),
            ("days", "pump", config.d_pump_period, config.d_pump_threshold),
            ("days", "dump", config.d_dump_period, config.d_dump_threshold),
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
                if max_candle.timestamp < min_candle.timestamp:
                     change = (p_min / p_max - 1) * 100
                     if abs(change) >= threshold:
                        alert_msg = f"📉 DUMP <b>{pair.symbol}</b> \n" \
                                    f"({pair.exchange}): {pair.source_label}\n" \
                                    f"<b>{change:+.0f}%</b> in {period_val} {period_type}\n" \
                                    f"Min: {p_min} | Max: {p_max}"
            
            elif direction_type == "pump":
                 if min_candle.timestamp < max_candle.timestamp:
                     change = (p_max / p_min - 1) * 100
                     if change >= threshold:
                        alert_msg = f"📈 PUMP <b>{pair.symbol}</b> \n" \
                                    f"({pair.exchange}): {pair.source_label}\n" \
                                    f"<b>{change:+.0f}%</b> in {period_val} {period_type}\n" \
                                    f"Min: {p_min} | Max: {p_max}"

            if alert_msg:
                await self._create_signal_if_new(session, SignalType.PRICE_CHANGE, alert_msg, config.dedup_hours)

    async def _check_volume_alerts(self, session: AsyncSession, pair: MonitoredPair, config: AlertConfig, rates: dict):
        """Проверка алертов по объему"""
        v_period = config.v_period
        v_threshold = config.v_threshold
        
        if not (v_period and v_threshold and v_threshold > 0):
            return

        now = datetime.utcnow()
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

        if total_v_usdt <= v_threshold * v_period:
            v_msg = f"📊 Low Volume <b>{pair.symbol}</b>\n" \
                    f"({pair.exchange}): {pair.source_label}\n" \
                    f"Volume in {v_period} days: {total_v_usdt:,.0f} USDT\n" \
                    f"<b>{total_v_usdt/v_period:,.0f}</b> USDT/day\n"    
            
            if rate != 1.0:
                v_msg += f" (quote {quote}: {rate})"
            
            v_msg += f"\nThreshold: {v_threshold:,.0f} USDT"
            await self._create_signal_if_new(session, SignalType.VOLUME_ALERT, v_msg, config.dedup_hours)

    async def _create_signal_if_new(self, session: AsyncSession, sig_type: SignalType, msg: str, dedup_hours: int):
        """Создает сигнал в БД и отправляет в ТГ, если такого сообщения еще не было за последние N часов"""
        cutoff_time = datetime.utcnow() - timedelta(hours=dedup_hours)
        stmt = select(Signal).where(
            Signal.type == sig_type,
            Signal.raw_message == msg,
            Signal.is_sent == True,
            Signal.created_at >= cutoff_time
        )
        existing = (await session.execute(stmt)).first()
        
        if not existing:
            new_sig = Signal(type=sig_type, raw_message=msg)
            session.add(new_sig)
            await session.commit()
            await session.refresh(new_sig)
            
            logger.warning(f"NEW ANALYSIS SIGNAL: {msg}")
            
            # Импорт внутри метода во избежание циклической зависимости
            from services.notifications import send_and_log_signal
            asyncio.create_task(send_and_log_signal(new_sig.id, msg, prefix=""))
