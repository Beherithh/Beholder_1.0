"""
Тесты моделей и Enums.

Фиксируют текущее поведение: приоритеты RiskLevel,
создание записей, связи между таблицами.
"""
import pytest
import pytest_asyncio
from datetime import datetime

from database.models import (
    RiskLevel, MonitoringStatus, SignalType, DelistingEventType,
    AppSettings, MonitoredPair, DelistingEvent, MarketData, Signal,
)


# ==================== Enum Tests ====================

class TestRiskLevel:
    """Проверяем порядок приоритетов RiskLevel."""

    def test_priority_order(self):
        """NORMAL < CROSS_RISK < CROSS_DELISTING < RISK_ZONE < DELISTING_PLANNED"""
        assert RiskLevel.NORMAL.priority == 0
        assert RiskLevel.CROSS_RISK.priority == 1
        assert RiskLevel.CROSS_DELISTING.priority == 2
        assert RiskLevel.RISK_ZONE.priority == 3
        assert RiskLevel.DELISTING_PLANNED.priority == 4

    def test_priority_comparison(self):
        """Высокий приоритет > низкого — для логики 'не понижать risk'."""
        assert RiskLevel.DELISTING_PLANNED.priority > RiskLevel.NORMAL.priority
        assert RiskLevel.RISK_ZONE.priority > RiskLevel.CROSS_RISK.priority

    def test_enum_values(self):
        """Значения enum совпадают со строками в БД."""
        assert RiskLevel.NORMAL.value == "normal"
        assert RiskLevel.DELISTING_PLANNED.value == "delisting_planned"


class TestSignalType:
    def test_all_values_exist(self):
        """Убеждаемся, что все типы сигналов на месте."""
        expected = {"price_change", "volume_alert", "delisting_warning", "st_warning", "rank_warning"}
        actual = {s.value for s in SignalType}
        assert actual == expected


class TestDelistingEventType:
    def test_values(self):
        assert DelistingEventType.DELISTING.value == "delisting"
        assert DelistingEventType.ST.value == "st"


# ==================== Model Creation Tests ====================

class TestMonitoredPair:
    @pytest.mark.asyncio
    async def test_create_pair(self, db_session):
        """Создание пары с дефолтными значениями."""
        pair = MonitoredPair(
            exchange="GATEIO",
            symbol="BTC/USDT",
            source_file="C:/data/gate.json",
        )
        db_session.add(pair)
        await db_session.commit()
        await db_session.refresh(pair)

        assert pair.id is not None  # Автоинкремент сработал
        assert pair.monitoring_status == MonitoringStatus.ACTIVE  # Дефолт
        assert pair.risk_level == RiskLevel.NORMAL  # Дефолт
        assert pair.cmc_rank is None  # Optional, дефолт None

    @pytest.mark.asyncio
    async def test_pair_with_market_data(self, db_session):
        """Связь MonitoredPair → MarketData через Relationship."""
        pair = MonitoredPair(
            exchange="BINANCE", symbol="ETH/USDT", source_file="test.json"
        )
        db_session.add(pair)
        await db_session.commit()
        await db_session.refresh(pair)

        candle = MarketData(
            pair_id=pair.id,
            timestamp=datetime(2025, 1, 1),
            open=3000.0, high=3100.0, low=2900.0, close=3050.0,
            volume=1000.0,
        )
        db_session.add(candle)
        await db_session.commit()

        # Проверяем, что свеча записалась с правильным pair_id
        assert candle.id is not None
        assert candle.pair_id == pair.id


class TestDelistingEvent:
    @pytest.mark.asyncio
    async def test_create_event(self, db_session):
        event = DelistingEvent(
            exchange="GATEIO",
            symbol="SCAM",
            announcement_title="Delisting SCAM_USDT",
            announcement_url="https://gate.io/article/123",
            type=DelistingEventType.DELISTING,
        )
        db_session.add(event)
        await db_session.commit()
        await db_session.refresh(event)

        assert event.id is not None
        assert event.found_at is not None  # default_factory=datetime.utcnow


class TestSignal:
    @pytest.mark.asyncio
    async def test_create_signal(self, db_session):
        signal = Signal(
            type=SignalType.DELISTING_WARNING,
            raw_message="⚠️ SCAM scheduled for delisting",
        )
        db_session.add(signal)
        await db_session.commit()
        await db_session.refresh(signal)

        assert signal.id is not None
        assert signal.is_sent is False  # Дефолт
        assert signal.sent_at is None  # Дефолт
        assert signal.pair_id is None  # Optional


class TestAppSettings:
    @pytest.mark.asyncio
    async def test_create_setting(self, db_session):
        setting = AppSettings(key="test_key", value="test_value")
        db_session.add(setting)
        await db_session.commit()

        result = await db_session.get(AppSettings, "test_key")
        assert result is not None
        assert result.value == "test_value"
