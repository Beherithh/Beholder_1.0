from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from loguru import logger
from sqlmodel import select

from database.models import Signal

if TYPE_CHECKING:
    from services.telegram import TelegramService


class NotificationService:
    """
    Сервис для отправки уведомлений через Telegram и записи в БД.

    Зависимости передаются явно через конструктор (Dependency Injection):
    нет глобального состояния, нет скрытых импортов.
    """

    def __init__(self, telegram: TelegramService, session_factory) -> None:
        """
        :param telegram: Экземпляр TelegramService для отправки сообщений.
        :param session_factory: Фабрика сессий БД (например, get_session).
        """
        self.telegram = telegram
        self.session_factory = session_factory

    async def send_and_log_signal(self, signal_id: int, message: str, prefix: str = "") -> None:
        """
        Отправляет сообщение в Telegram и помечает сигнал как отправленный.

        :param signal_id: ID записи Signal в БД.
        :param message: Тело сообщения.
        :param prefix: Необязательный префикс (выводится жирным шрифтом).
        """
        full_msg = f"<b>{prefix}</b>\n{message}" if prefix else message

        try:
            sent = await self.telegram.send_message(full_msg)
        except Exception as e:
            logger.error(f"Failed to send telegram message: {e}")
            sent = False

        if not sent:
            logger.warning(f"Telegram message not sent for signal {signal_id}: {full_msg[:80]}...")
            return

        async with self.session_factory() as session:
            stmt = select(Signal).where(Signal.id == signal_id)
            result = await session.execute(stmt)
            sig = result.scalar_one_or_none()
            if sig:
                sig.is_sent = True
                session.add(sig)
                await session.commit()
                logger.info(f"Signal {signal_id} marked as sent.")
            else:
                logger.error(f"Signal {signal_id} not found when trying to mark as sent.")
