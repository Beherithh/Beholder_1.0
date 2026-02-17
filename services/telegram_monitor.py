from loguru import logger
from sqlmodel import select

from database.models import AppSettings, DelistingEvent, DelistingEventType
from services.article_parser import ArticleParser

class TelegramMonitorService:
    """
    Сервис для мониторинга Telegram-каналов на предмет анонсов.
    """

    def __init__(self, session_factory, article_parser: ArticleParser):
        self.session_factory = session_factory
        self.article_parser = article_parser

    async def check_binance_telegram_channel(self) -> int:
        """
        Читает последние сообщения из канала @binance_announcements через Pyrogram.
        Возвращает количество найденных новых событий.
        """
        try:
            from pyrogram import Client
        except ImportError:
            logger.error("Pyrogram не установлен. Используйте: uv add pyrogram")
            return 0
        
        # Получаем конфиг через ConfigService
        from services.system import get_config_service
        tg_conf = await get_config_service().get_telegram_config()
        
        if not tg_conf.api_id or not tg_conf.api_hash:
            logger.warning("Telegram API credentials не настроены. Пропуск проверки @binance_announcements")
            return 0
            
        async with self.session_factory() as session:
            # Get last processed message ID
            last_msg_id_setting = await session.get(AppSettings, "binance_tg_last_message_id")
            last_msg_id = int(last_msg_id_setting.value) if last_msg_id_setting and last_msg_id_setting.value else 0
            
            logger.info(f"Checking @binance_announcements (last message ID: {last_msg_id})...")
            
            new_events = 0
            latest_id = last_msg_id
            
            try:
                # Create Pyrogram client
                app = Client(
                    "beholder_telegram",
                    api_id=int(tg_conf.api_id),
                    api_hash=tg_conf.api_hash,
                    workdir="."
                )
                
                async with app:
                    # Read last 100 messages from channel
                    messages_count = 0
                    async for message in app.get_chat_history("binance_announcements", limit=100):
                        messages_count += 1
                        
                        if message.id <= last_msg_id:
                            break  # Already processed
                        
                        if message.id > latest_id:
                            latest_id = message.id
                        
                        # Support both text and caption (for images)
                        content = message.text or message.caption or ""
                        
                        if not content:
                            continue
                        
                        text_lower = content.lower()
                        
                        # Check ignore keywords first
                        if any(kw in text_lower for kw in self.article_parser.IGNORE_KEYWORDS):
                            continue
                        
                        # Check for delisting OR ST/Monitoring Tag keywords
                        is_relevant = any(k in text_lower for k in self.article_parser.DELIST_TRIGGER_KEYWORDS) or \
                                      any(k.lower() in text_lower for k in self.article_parser.ST_TRIGGER_KEYWORDS)
                        
                        if not is_relevant:
                            continue
                        
                        logger.info(f"[BINANCE-TG] Processing message #{message.id}: {content[:100]}...")
                        
                        # Extract pairs using ArticleParser
                        pairs = self.article_parser.extract_pairs_from_text(content)
                        
                        if not pairs:
                            logger.debug(f"[BINANCE-TG] No pairs found in message #{message.id}")
                            continue
                        
                        # Determine event type
                        event_type = DelistingEventType.DELISTING if any(k in text_lower for k in self.article_parser.DELIST_TRIGGER_KEYWORDS) else DelistingEventType.ST
                        
                        # Store in database
                        for symbol in pairs:
                            # Check if already exists
                            stmt = select(DelistingEvent).where(
                                DelistingEvent.exchange == "BINANCE",
                                DelistingEvent.symbol == symbol,
                                DelistingEvent.announcement_url == f"https://t.me/binance_announcements/{message.id}"
                            )
                            existing = (await session.execute(stmt)).first()
                            
                            if not existing:
                                event = DelistingEvent(
                                    exchange="BINANCE",
                                    symbol=symbol,
                                    announcement_title=content[:200],  # First 200 chars as title
                                    announcement_url=f"https://t.me/binance_announcements/{message.id}",
                                    type=event_type
                                )
                                session.add(event)
                                new_events += 1
                                event_label = "delisting" if event_type == DelistingEventType.DELISTING else "ST/Monitoring Tag"
                                logger.info(f"[BINANCE-TG] New {event_label}: {symbol}")
                        
                        if new_events > 0:
                            await session.commit()
                
                # Update last processed message ID
                if latest_id > last_msg_id:
                    if not last_msg_id_setting:
                        last_msg_id_setting = AppSettings(key="binance_tg_last_message_id", value=str(latest_id))
                        session.add(last_msg_id_setting)
                    else:
                        last_msg_id_setting.value = str(latest_id)
                    await session.commit()
                    logger.info(f"[BINANCE-TG] Updated last message ID to {latest_id}")
                
                logger.info(f"[BINANCE-TG] Scanned {messages_count} messages. Found {new_events} new delisting events.")
                return new_events
                
            except Exception as e:
                logger.error(f"[BINANCE-TG] Error reading channel: {e}")
                return 0
