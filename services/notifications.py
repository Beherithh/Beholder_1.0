import asyncio
from loguru import logger
from sqlmodel import select

from database.core import get_session
from database.models import Signal
from services.system import get_telegram_service

async def send_and_log_signal(signal_id: int, message: str, prefix: str = "") -> None:
    """Send a Telegram message and, on success, mark the corresponding Signal as sent.

    Args:
        signal_id: ID of the Signal record in the database.
        message: The main message body.
        prefix: Optional prefix (e.g., "[ALERT]") that will be rendered in bold.
    """
    # Build final message with optional prefix
    full_msg = f"<b>{prefix}</b>\n{message}" if prefix else message

    # Send via Telegram service
    try:
        telegram = get_telegram_service()
        sent = await telegram.send_message(full_msg)
    except Exception as e:
        logger.error(f"Failed to obtain Telegram service or send message: {e}")
        sent = False

    if not sent:
        logger.warning(f"Telegram message not sent for signal {signal_id}: {full_msg}")
        return

    # Update the Signal record to reflect successful delivery
    async with get_session() as session:
        stmt = select(Signal).where(Signal.id == signal_id)
        result = await session.execute(stmt)
        sig = result.scalar_one_or_none()
        if sig:
            sig.is_sent = True
            session.add(sig)
            await session.commit()
            logger.info(f"Signal {signal_id} marked as sent.")
        else:
            logger.error(f"Signal with id {signal_id} not found when trying to mark as sent.")
