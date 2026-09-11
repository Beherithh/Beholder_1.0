"""
Сквозной интеграционный тест пайплайна рыночных алертов (Pipeline Integration Test).

Проверяет полную бизнес-цепочку:
1. Запись свечей котировок (MarketData) в базу данных SQLite.
2. Анализ данных модулем AlertEngine.
3. Обнаружение ценовой аномалии (PUMP > 50%).
4. Создание записи в таблице Signal со статусом is_sent = False.
5. Вызов NotificationService -> форматирование сообщения.
6. Фиксация успешной отправки в БД (is_sent = True).
"""

import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MonitoredPair, MarketData, Signal, SignalType
from services.alert_engine import AlertEngine
from services.notifications import NotificationService
from services.config import AlertConfig


@pytest.mark.asyncio
class TestMarketAlertPipeline:
    """Сквозное тестирование пайплайна: Свечи -> Анализ -> Сигнал -> Отправка."""

    async def test_pump_detection_and_notification_pipeline(
        self,
        db_session: AsyncSession,
        session_factory,
        create_pair,
    ) -> None:
        """
        Тестирует полный цикл:
        1. Создаём пару BTC/USDT.
        2. Записываем 2 свечи:
           - 2 часа назад: цена 50 000$
           - 1 час назад: цена 100 000$ (+100% PUMP)
        3. Запускаем AlertEngine.analyze_pair().
        4. Проверяем:
           - Сигнал записан в таблицу Signal.
           - NotificationService отправил сообщение через TelegramService.
           - Статус сигнала в БД обновлён на is_sent = True.
        """
        # 1. Создаем пару для мониторинга в БД
        pair: MonitoredPair = await create_pair(symbol="BTC/USDT", exchange="BINANCE")

        # 2. Формируем свечи с явным пампом цены
        now = datetime.now(timezone.utc)
        candle_old = MarketData(
            pair_id=pair.id,
            timestamp=now - timedelta(hours=2),
            open=50000.0,
            high=51000.0,
            low=49000.0,
            close=50000.0,
            volume=1000.0,
        )
        candle_new = MarketData(
            pair_id=pair.id,
            timestamp=now - timedelta(hours=1),
            open=50000.0,
            high=102000.0,  # Рост более чем на 100%
            low=50000.0,
            close=100000.0,
            volume=5000.0,
        )
        db_session.add_all([candle_old, candle_new])
        await db_session.commit()

        # 3. Настраиваем NotificationService с моком сетевого Telegram-клиента
        mock_telegram = MagicMock()
        mock_telegram.send_message = AsyncMock(return_value=True)
        notification_service = NotificationService(telegram=mock_telegram, session_factory=session_factory)

        # 4. Инициализируем настоящий AlertEngine
        alert_engine = AlertEngine(
            session_factory=session_factory,
            notification_service=notification_service,
        )

        # Конфигурация: порог пампа за 6 часов = 50%
        alert_config = AlertConfig(
            h_pump_period=6,
            h_pump_threshold=50.0,
            h_dump_period=6,
            h_dump_threshold=50.0,
        )

        # 5. Запуск анализа пары
        await alert_engine.analyze_pair(
            session=db_session,
            pair=pair,
            config=alert_config,
            rates={"USDT": 1.0},
        )

        # Ждем выполнения фоновой отправки уведомления (asyncio.create_task)
        await asyncio.sleep(0.1)

        # 6. ВЕРИФИКАЦИЯ РЕЗУЛЬТАТОВ:
        # А) Проверяем, что в БД создан сигнал (используем fresh select)
        async with session_factory() as verify_session:
            stmt = select(Signal).where(Signal.pair_id == pair.id)
            signals = (await verify_session.execute(stmt)).scalars().all()
        assert len(signals) == 1, "Должен быть создан ровно 1 сигнал"

        signal: Signal = signals[0]
        assert signal.type == SignalType.PRICE_CHANGE
        assert "PUMP" in signal.raw_message
        assert signal.is_sent is True, "Сигнал обязан быть помечен как отправленный (is_sent=True)"

        # Б) Проверяем, что TelegramService получил вызов с текстом сообщения
        assert mock_telegram.send_message.called
        sent_message = mock_telegram.send_message.call_args[0][0]
        assert "BTC/USDT" in sent_message
        assert "PUMP" in sent_message
