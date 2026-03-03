"""
Тесты ScraperService.

Покрываем бизнес-логику без внешних вызовов (Selenium, API):
  1. _update_pair_risk — обновление risk_level, создание сигналов, защита от понижения
  2. match_monitored_pairs_with_events — кросс-матч пар и событий делистинга
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from sqlmodel import select

from database.models import (
    MonitoredPair, MonitoringStatus, RiskLevel, Signal, SignalType,
    DelistingEvent, DelistingEventType,
)
from services.scraper import ScraperService


class TestUpdatePairRisk:
    """Тесты _update_pair_risk — ядро логики риска."""

    @pytest.mark.parametrize("initial_risk, new_risk, expected_risk, should_change", [
        (RiskLevel.NORMAL, RiskLevel.RISK_ZONE, RiskLevel.RISK_ZONE, True),
        (RiskLevel.DELISTING_PLANNED, RiskLevel.RISK_ZONE, RiskLevel.DELISTING_PLANNED, False),
        (RiskLevel.NORMAL, RiskLevel.NORMAL, RiskLevel.NORMAL, False),
    ])
    @pytest.mark.asyncio
    async def test_update_pair_risk_scenarios(
        self, session_factory, db_session, initial_risk, new_risk, expected_risk, should_change
    ):
        """Тестирование переходов уровней риска (включая защиту от понижения)."""
        pair = MonitoredPair(
            exchange="GATEIO", symbol="TEST/USDT", source_file="test.json",
            risk_level=initial_risk,
        )
        db_session.add(pair)
        await db_session.commit()
        await db_session.refresh(pair)

        service = ScraperService(session_factory, file_watcher=MagicMock(), config_service=MagicMock(), notification_service=MagicMock())

        with patch.object(service.notification_service, 'send_and_log_signal', new_callable=AsyncMock):
            changed = await service._update_pair_risk(
                db_session, pair, new_risk,
                SignalType.ST_WARNING, f"⚠️ ST WARNING! {pair.symbol}"
            )

        assert changed == should_change
        assert pair.risk_level == expected_risk

    @pytest.mark.asyncio
    async def test_no_duplicate_signal(self, session_factory, db_session):
        """Одинаковый сигнал (sent=True) не создаётся повторно."""
        pair = MonitoredPair(
            exchange="GATEIO", symbol="DUP/USDT", source_file="test.json",
        )
        db_session.add(pair)
        await db_session.commit()
        await db_session.refresh(pair)

        existing_signal = Signal(
            type=SignalType.DELISTING_WARNING,
            pair_id=pair.id,
            raw_message="⚠️ DELIST DUP/USDT",
            is_sent=True,
        )
        db_session.add(existing_signal)
        await db_session.commit()
        await db_session.refresh(pair)

        service = ScraperService(session_factory, file_watcher=MagicMock(), config_service=MagicMock(), notification_service=MagicMock())

        with patch.object(service.notification_service, 'send_and_log_signal', new_callable=AsyncMock):
            changed = await service._update_pair_risk(
                db_session, pair, RiskLevel.DELISTING_PLANNED,
                SignalType.DELISTING_WARNING, "⚠️ DELIST DUP/USDT"
            )

        result = await db_session.execute(select(Signal))
        signals = result.scalars().all()
        assert len(signals) == 1  # Только исходный


class TestMatchMonitoredPairsWithEvents:
    """Тесты кросс-матчинга пар и событий делистинга."""

    @pytest.mark.asyncio
    async def test_direct_delisting_match(self, session_factory, db_session):
        """Пара на той же бирже, где делистинг → DELISTING_PLANNED."""
        pair = MonitoredPair(
            exchange="GATEIO", symbol="SCAM/USDT", source_file="test.json",
            source_label='["Gate 1"]',
        )
        event = DelistingEvent(
            exchange="GATEIO", symbol="SCAM",
            announcement_title="Delisting SCAM",
            announcement_url="https://gate.io/123",
            type=DelistingEventType.DELISTING,
        )
        db_session.add(pair)
        db_session.add(event)
        await db_session.commit()

        service = ScraperService(session_factory, file_watcher=MagicMock(), config_service=MagicMock(), notification_service=MagicMock())

        with patch.object(service.notification_service, 'send_and_log_signal', new_callable=AsyncMock):
            updated = await service.match_monitored_pairs_with_events(db_session)

        assert updated >= 1

        await db_session.refresh(pair)
        assert pair.risk_level == RiskLevel.DELISTING_PLANNED

    @pytest.mark.asyncio
    async def test_cross_exchange_st(self, session_factory, db_session):
        """ST-событие на другой бирже → CROSS_RISK."""
        pair = MonitoredPair(
            exchange="GATEIO", symbol="RISKY/USDT", source_file="test.json",
            source_label='["Gate 1"]',
        )
        event = DelistingEvent(
            exchange="BINANCE", symbol="RISKY",
            announcement_title="ST warning",
            announcement_url="https://binance.com/456",
            type=DelistingEventType.ST,
        )
        db_session.add(pair)
        db_session.add(event)
        await db_session.commit()

        service = ScraperService(session_factory, file_watcher=MagicMock(), config_service=MagicMock(), notification_service=MagicMock())

        with patch.object(service.notification_service, 'send_and_log_signal', new_callable=AsyncMock):
            await service.match_monitored_pairs_with_events(db_session)

        await db_session.refresh(pair)
        assert pair.risk_level == RiskLevel.CROSS_RISK

    @pytest.mark.asyncio
    async def test_no_match_when_no_events(self, session_factory, db_session):
        """Нет событий — пары остаются NORMAL."""
        pair = MonitoredPair(
            exchange="GATEIO", symbol="SAFE/USDT", source_file="test.json",
            source_label='["Gate 1"]',
        )
        db_session.add(pair)
        await db_session.commit()

        service = ScraperService(session_factory, file_watcher=MagicMock(), config_service=MagicMock(), notification_service=MagicMock())
        updated = await service.match_monitored_pairs_with_events(db_session)

        assert updated == 0
        await db_session.refresh(pair)
        assert pair.risk_level == RiskLevel.NORMAL
