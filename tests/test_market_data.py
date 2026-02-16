"""
Тесты MarketDataService.

Покрываем:
  1. _create_signal_if_new — дедупликация сигналов по времени
  2. _check_price_vol_alerts — генерация алертов (pump/dump) по свечам
"""
import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timedelta
from sqlmodel import select

from database.models import (
    MonitoredPair, MarketData, Signal, SignalType, AppSettings,
)
from services.market_data import MarketDataService


class TestCreateSignalIfNew:
    """Дедупликация: не создаёт сигнал, если аналогичный уже отправлен недавно."""

    @pytest.mark.asyncio
    async def test_creates_new_signal(self, session_factory, db_session):
        """В пустой БД — сигнал создаётся."""
        db_session.add(AppSettings(key="alert_dedup_hours", value="12"))
        await db_session.commit()

        service = MarketDataService(session_factory)

        with patch("services.notifications.send_and_log_signal", new_callable=AsyncMock):
            await service._create_signal_if_new(
                db_session, SignalType.PRICE_CHANGE, "📈 PUMP BTC/USDT +50%"
            )

        result = await db_session.execute(select(Signal))
        signals = result.scalars().all()
        assert len(signals) == 1
        assert signals[0].raw_message == "📈 PUMP BTC/USDT +50%"

    @pytest.mark.asyncio
    async def test_skips_duplicate_within_window(self, session_factory, db_session):
        """Не создаёт дубликат, если такой же сигнал отправлен < N часов назад."""
        db_session.add(AppSettings(key="alert_dedup_hours", value="12"))

        existing = Signal(
            type=SignalType.PRICE_CHANGE,
            raw_message="📈 PUMP BTC/USDT +50%",
            is_sent=True,
            created_at=datetime.utcnow() - timedelta(hours=1),
        )
        db_session.add(existing)
        await db_session.commit()

        service = MarketDataService(session_factory)

        with patch("services.notifications.send_and_log_signal", new_callable=AsyncMock):
            await service._create_signal_if_new(
                db_session, SignalType.PRICE_CHANGE, "📈 PUMP BTC/USDT +50%"
            )

        result = await db_session.execute(select(Signal))
        signals = result.scalars().all()
        assert len(signals) == 1  # Только исходный


class TestCheckPriceVolAlerts:
    """Тесты генерации алертов Pump/Dump по свечам."""

    async def _setup_pair_with_candles(
        self, db_session, symbol: str,
        candles_data: list[tuple[datetime, float, float, float, float, float]],
    ) -> MonitoredPair:
        """Хелпер: создаёт пару и свечи в БД."""
        pair = MonitoredPair(
            exchange="GATEIO", symbol=symbol, source_file="test.json",
            source_label='["Test"]',
        )
        db_session.add(pair)
        await db_session.commit()
        await db_session.refresh(pair)

        for ts, o, h, l, c, v in candles_data:
            candle = MarketData(
                pair_id=pair.id, timestamp=ts,
                open=o, high=h, low=l, close=c, volume=v,
            )
            db_session.add(candle)
        await db_session.commit()

        return pair

    @pytest.mark.asyncio
    async def test_pump_alert(self, session_factory, db_session):
        """Рост +100% за период → генерирует PUMP-алерт."""
        db_session.add(AppSettings(key="alert_dedup_hours", value="12"))
        await db_session.commit()

        now = datetime.utcnow()
        pair = await self._setup_pair_with_candles(db_session, "MOON/USDT", [
            (now - timedelta(hours=5), 1.0, 1.1, 0.9, 1.0, 100),  # low=0.9
            (now - timedelta(hours=1), 1.8, 2.0, 1.7, 1.9, 200),  # high=2.0
        ])

        config = {
            "h_pump_period": 6, "h_pump_threshold": 50,
            "h_dump_period": 6, "h_dump_threshold": 50,
            "d_pump_period": None, "d_pump_threshold": None,
            "d_dump_period": None, "d_dump_threshold": None,
            "v_period": None, "v_threshold": 0,
        }

        service = MarketDataService(session_factory)

        with patch("services.notifications.send_and_log_signal", new_callable=AsyncMock):
            await service._check_price_vol_alerts(db_session, pair, config, rates={})

        result = await db_session.execute(select(Signal))
        signals = result.scalars().all()
        # Pump: min=0.9 → max=2.0 → +122% > 50% порог
        assert len(signals) >= 1
        assert any("PUMP" in s.raw_message for s in signals)

    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self, session_factory, db_session):
        """Изменение < порога → алерт НЕ генерируется."""
        db_session.add(AppSettings(key="alert_dedup_hours", value="12"))
        await db_session.commit()

        now = datetime.utcnow()
        pair = await self._setup_pair_with_candles(db_session, "FLAT/USDT", [
            (now - timedelta(hours=3), 1.0, 1.05, 0.95, 1.0, 100),
            (now - timedelta(hours=1), 1.0, 1.03, 0.97, 1.01, 100),
        ])

        config = {
            "h_pump_period": 6, "h_pump_threshold": 50,
            "h_dump_period": 6, "h_dump_threshold": 50,
            "d_pump_period": None, "d_pump_threshold": None,
            "d_dump_period": None, "d_dump_threshold": None,
            "v_period": None, "v_threshold": 0,
        }

        service = MarketDataService(session_factory)

        with patch("services.notifications.send_and_log_signal", new_callable=AsyncMock):
            await service._check_price_vol_alerts(db_session, pair, config, rates={})

        result = await db_session.execute(select(Signal))
        signals = result.scalars().all()
        assert len(signals) == 0
