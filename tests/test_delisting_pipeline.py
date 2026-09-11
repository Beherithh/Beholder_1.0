"""
Сквозной интеграционный тест пайплайна делистингов и рисков (Delisting Pipeline Test).

Проверяет совместную работу:
1. FileWatcherService: синхронизация списка пар из JSON-файла в базу SQLite.
2. DelistingEvent: регистрация события делистинга (напрямую или через парсер анонсов).
3. ScraperService (match_monitored_pairs_with_events):
   - Прямой делистинг (Direct): повышение риска пары до DELISTING_PLANNED.
   - Кросс-биржевой делистинг (Cross): повышение риска до CROSS_DELISTING при анонсе на другой бирже.
4. NotificationService: генерация и отправка алерта в Telegram.
5. ScraperService (demote_orphaned_risks): автоматический сброс риска до NORMAL при удалении события.
"""

import json
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    MonitoredPair, MonitoringStatus, RiskLevel,
    DelistingEvent, DelistingEventType, Signal, SignalType, MarketType
)
from services.file_watcher import FileWatcherService
from services.scraper import ScraperService
from services.notifications import NotificationService
from services.config import ConfigService


@pytest.mark.asyncio
class TestDelistingPipeline:
    """Сквозное тестирование цепочки обнаружения и распространения рисков делистинга."""

    async def test_file_sync_to_delisting_alert_pipeline(
        self,
        tmp_path: Path,
        session_factory,
        db_session: AsyncSession,
        config_service: ConfigService,
    ) -> None:
        """
        Полный сквозной сценарий:
        1. Создаём реальный JSON-файл со списком пар биржи Gate.io.
        2. FileWatcherService загружает его в SQLite.
        3. В базу поступает событие о делистинге монеты с Gate.io.
        4. ScraperService матчит пары и повышает риск до DELISTING_PLANNED.
        5. Создаётся сигнал и отправляется в Telegram.
        """
        # --- ШАГ 1: Создаём тестовый JSON файл с парами ---
        json_file = tmp_path / "Gate_instruments_USDT.json"
        json_file.write_text(
            json.dumps({"listHelper": [{"symbol": "ALPHA_USDT"}, {"symbol": "BETA_USDT"}]}),
            encoding="utf-8"
        )

        file_watcher = FileWatcherService(session_factory=session_factory, config_service=config_service)
        sync_stats = await file_watcher.sync_files([{"path": str(json_file), "name": "Gate Spot"}])
        assert sync_stats["added"] == 2

        # Проверяем, что монеты попали в базу с нормальным риском
        stmt = select(MonitoredPair).where(MonitoredPair.symbol == "ALPHA/USDT")
        pair = (await db_session.execute(stmt)).scalar_one()
        assert pair.risk_level == RiskLevel.NORMAL
        assert pair.monitoring_status == MonitoringStatus.ACTIVE

        # --- ШАГ 2: В базу поступает событие делистинга монеты ALPHA ---
        delisting_event = DelistingEvent(
            exchange="GATEIO",
            symbol="ALPHA",
            type=DelistingEventType.DELISTING,
            announcement_url="https://gate.io/article/12345",
            announcement_title="Gate.io Will Delist ALPHA",
        )
        db_session.add(delisting_event)
        await db_session.commit()

        # --- ШАГ 3: Запуск матчинга через ScraperService ---
        mock_telegram = MagicMock()
        mock_telegram.send_message = AsyncMock(return_value=True)
        notification_service = NotificationService(telegram=mock_telegram, session_factory=session_factory)

        scraper_service = ScraperService(
            session_factory=session_factory,
            file_watcher=file_watcher,
            config_service=config_service,
            notification_service=notification_service,
        )

        await scraper_service.match_monitored_pairs_with_events(session=db_session)
        await db_session.commit()

        # Даём корутине отправки завершиться
        await asyncio.sleep(0.1)

        # --- ШАГ 4: Проверка результатов ---
        # А) Риск пары повышен до DELISTING_PLANNED
        await db_session.refresh(pair)
        assert pair.risk_level == RiskLevel.DELISTING_PLANNED

        # Б) Создан сигнал DELISTING_WARNING
        sig_stmt = select(Signal).where(Signal.pair_id == pair.id)
        signals = (await db_session.execute(sig_stmt)).scalars().all()
        assert len(signals) == 1
        assert signals[0].type == SignalType.DELISTING_WARNING
        assert "DELISTING" in signals[0].raw_message

        # В) Уведомление ушло в Telegram
        assert mock_telegram.send_message.called
        sent_msg = mock_telegram.send_message.call_args[0][0]
        assert "ALPHA" in sent_msg

    async def test_cross_exchange_risk_propagation(
        self,
        session_factory,
        db_session: AsyncSession,
        config_service: ConfigService,
        create_pair,
    ) -> None:
        """
        Кросс-биржевой риск:
        Если пара торгуется на Gate.io, а новость о делистинге вышла на Binance,
        риск на Gate.io должен стать CROSS_DELISTING (а не прямой DELISTING_PLANNED).
        """
        pair: MonitoredPair = await create_pair(symbol="DOGE/USDT", exchange="GATEIO")

        # Событие делистинга на ДРУГОЙ бирже (BINANCE)
        cross_event = DelistingEvent(
            exchange="BINANCE",
            symbol="DOGE",
            type=DelistingEventType.DELISTING,
            announcement_url="https://binance.com/delist",
            announcement_title="Binance Delisting Notice",
        )
        db_session.add(cross_event)
        await db_session.commit()

        mock_telegram = MagicMock()
        mock_telegram.send_message = AsyncMock(return_value=True)
        notification_service = NotificationService(telegram=mock_telegram, session_factory=session_factory)

        scraper_service = ScraperService(
            session_factory=session_factory,
            file_watcher=MagicMock(),
            config_service=config_service,
            notification_service=notification_service,
        )

        await scraper_service.match_monitored_pairs_with_events(session=db_session)
        await db_session.commit()

        await db_session.refresh(pair)
        assert pair.risk_level == RiskLevel.CROSS_DELISTING, "Риск должен стать CROSS_DELISTING"

    async def test_demote_orphaned_risks_when_event_removed(
        self,
        session_factory,
        db_session: AsyncSession,
        config_service: ConfigService,
        create_pair,
    ) -> None:
        """
        Восстановление риска (Demote):
        Если пара имела высокий риск, но DelistingEvent был удалён из БД,
        метод demote_orphaned_risks должен автоматически вернуть статус в NORMAL.
        """
        pair: MonitoredPair = await create_pair(symbol="SAFE/USDT", exchange="GATEIO")
        pair.risk_level = RiskLevel.DELISTING_PLANNED
        db_session.add(pair)
        await db_session.commit()

        scraper_service = ScraperService(
            session_factory=session_factory,
            file_watcher=MagicMock(),
            config_service=config_service,
            notification_service=MagicMock(),
        )

        # Запускаем очистку осиротевших рисков (событий в БД нет)
        await scraper_service.demote_orphaned_risks(session=db_session)

        await db_session.refresh(pair)
        assert pair.risk_level == RiskLevel.NORMAL, "Риск должен быть сброшен обратно в NORMAL"
