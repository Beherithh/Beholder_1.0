"""
Тесты AlertEngine.

Покрываем:
  1. _create_signal_if_new — дедупликация сигналов по времени
  2. _check_price_alerts — генерация алертов (pump/dump) по свечам
"""
import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timedelta
from sqlmodel import select

from database.models import (
    MonitoredPair, MarketData, Signal, SignalType
)
from services.alert_engine import AlertEngine
from services.config import AlertConfig


class TestCreateSignalIfNew:
    """Дедупликация: не создаёт сигнал, если аналогичный уже отправлен недавно."""

    @pytest.mark.asyncio
    async def test_creates_new_signal(self, session_factory, db_session, setup_defaults):
        """В пустой БД — сигнал создаётся."""
        await setup_defaults()
        service = AlertEngine(session_factory)

        with patch("services.notifications.send_and_log_signal", new_callable=AsyncMock):
            await service._create_signal_if_new(
                db_session, SignalType.PRICE_CHANGE, "📈 PUMP BTC/USDT +50%", dedup_hours=12
            )

        result = await db_session.execute(select(Signal))
        signals = result.scalars().all()
        assert len(signals) == 1
        assert signals[0].raw_message == "📈 PUMP BTC/USDT +50%"

    @pytest.mark.asyncio
    async def test_skips_duplicate_within_window(self, session_factory, db_session, setup_defaults):
        """Не создаёт дубликат, если такой же сигнал отправлен < N часов назад."""
        await setup_defaults()

        existing = Signal(
            type=SignalType.PRICE_CHANGE,
            raw_message="📈 PUMP BTC/USDT +50%",
            is_sent=True,
            created_at=datetime.utcnow() - timedelta(hours=1),
        )
        db_session.add(existing)
        await db_session.commit()

        service = AlertEngine(session_factory)

        with patch("services.notifications.send_and_log_signal", new_callable=AsyncMock):
            await service._create_signal_if_new(
                db_session, SignalType.PRICE_CHANGE, "📈 PUMP BTC/USDT +50%", dedup_hours=12
            )

        result = await db_session.execute(select(Signal))
        signals = result.scalars().all()
        assert len(signals) == 1  # Только исходный


class TestCheckPriceAlerts:
    """Тесты генерации алертов Pump/Dump по свечам."""

    @pytest.mark.asyncio
    async def test_pump_alert(self, session_factory, db_session, setup_defaults, create_pair, config_service):
        """Рост +100% за период → генерирует PUMP-алерт."""
        await setup_defaults()

        now = datetime.utcnow()
        pair = await create_pair(symbol="MOON/USDT", exchange="GATEIO")
        
        candles = [
            (now - timedelta(hours=5), 1.0, 1.1, 0.9, 1.0, 100),  # low=0.9
            (now - timedelta(hours=1), 1.8, 2.0, 1.7, 1.9, 200),  # high=2.0
        ]
        for ts, o, h, l, c, v in candles:
            candle = MarketData(
                pair_id=pair.id, timestamp=ts,
                open=o, high=h, low=l, close=c, volume=v,
            )
            db_session.add(candle)
        await db_session.commit()

        config = await config_service.get_alert_config()

        service = AlertEngine(session_factory)

        with patch("services.notifications.send_and_log_signal", new_callable=AsyncMock):
            # Используем _check_price_alerts, так как логика теперь там
            await service._check_price_alerts(db_session, pair, config)

        result = await db_session.execute(select(Signal))
        signals = result.scalars().all()
        # Pump: min=0.9 → max=2.0 → +122% > 50% порог
        assert len(signals) >= 1
        assert any("PUMP" in s.raw_message for s in signals)

    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self, session_factory, db_session, setup_defaults, create_pair, config_service):
        """Изменение < порога → алерт НЕ генерируется."""
        await setup_defaults()

        now = datetime.utcnow()
        pair = await create_pair(symbol="FLAT/USDT", exchange="GATEIO")
        
        candles = [
            (now - timedelta(hours=3), 1.0, 1.05, 0.95, 1.0, 100),
            (now - timedelta(hours=1), 1.0, 1.03, 0.97, 1.01, 100),
        ]
        for ts, o, h, l, c, v in candles:
            candle = MarketData(
                pair_id=pair.id, timestamp=ts,
                open=o, high=h, low=l, close=c, volume=v,
            )
            db_session.add(candle)
        await db_session.commit()

        config = await config_service.get_alert_config()

        service = AlertEngine(session_factory)

        with patch("services.notifications.send_and_log_signal", new_callable=AsyncMock):
            await service._check_price_alerts(db_session, pair, config)

        result = await db_session.execute(select(Signal))
        signals = result.scalars().all()
        assert len(signals) == 0
