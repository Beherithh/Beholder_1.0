import os
import asyncio
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
        Основной метод-оркестратор.
        Проверяет настройки, управляет таймаутом и запускает сканирование.
        """
        # 1. Проверка наличия библиотеки
        try:
            from pyrogram import Client
        except ImportError:
            logger.error("Pyrogram не установлен. Используйте: uv add pyrogram")
            return 0
        
        # 2. Проверка конфигурации
        from services.system import get_config_service
        tg_conf = await get_config_service().get_telegram_config()
        
        if not tg_conf.api_id or not tg_conf.api_hash:
            logger.warning("Telegram API credentials не настроены. Пропуск проверки @binance_announcements")
            return 0
            
        # 3. Проверка файла сессии
        session_file = "beholder_telegram.session"
        if not os.path.exists(session_file):
            logger.warning(f"Файл сессии '{session_file}' не найден. Создайте сессию через Настройки -> Telegram API.")
            return 0

        # 4. Запуск сканирования с таймаутом
        try:
            async with self.session_factory() as session:
                # Оборачиваем вызов приватного метода в wait_for
                return await asyncio.wait_for(
                    self._scan_messages(session, tg_conf),
                    timeout=60.0
                )
        except asyncio.TimeoutError:
            logger.error("[BINANCE-TG] Timeout connecting to Telegram (60s). Skipping.")
            return 0
        except Exception as e:
            logger.error(f"[BINANCE-TG] Error reading channel: {e}")
            return 0

    async def _scan_messages(self, session, tg_conf) -> int:
        """
        Внутренняя логика: подключение к TG, парсинг сообщений, сохранение в БД.
        """
        from pyrogram import Client # Импорт здесь безопасен, т.к. проверен выше

        # Получаем ID последнего обработанного сообщения
        last_msg_id_setting = await session.get(AppSettings, "binance_tg_last_message_id")
        last_msg_id = int(last_msg_id_setting.value) if last_msg_id_setting and last_msg_id_setting.value else 0

        logger.info(f"Checking @binance_announcements (last message ID: {last_msg_id})...")

        new_events = 0
        latest_id = last_msg_id

        # Инициализация клиента
        app = Client(
            "beholder_telegram",
            api_id=int(tg_conf.api_id),
            api_hash=tg_conf.api_hash,
            workdir="."
        )

        async with app:
            messages_count = 0
            # Читаем последние 100 сообщений
            async for message in app.get_chat_history("binance_announcements", limit=100):
                messages_count += 1

                if message.id <= last_msg_id:
                    break  # Дошли до уже обработанных
                
                if message.id > latest_id:
                    latest_id = message.id
                
                # Обработка одного сообщения
                if await self._process_single_message(session, message):
                    new_events += 1

        # Обновляем ID последнего сообщения в настройках
        if latest_id > last_msg_id:
            if not last_msg_id_setting:
                new_setting = AppSettings(key="binance_tg_last_message_id", value=str(latest_id))
                session.add(new_setting)
            else:
                last_msg_id_setting.value = str(latest_id)
            await session.commit()
            logger.info(f"[BINANCE-TG] Updated last message ID to {latest_id}")

        logger.info(f"[BINANCE-TG] Scanned {messages_count} messages. Found {new_events} new delisting events.")
        return new_events

    async def _process_single_message(self, session, message) -> bool:
        """
        Парсит одно сообщение и сохраняет события в БД.
        Возвращает True, если были найдены новые события.
        """
        content = message.text or message.caption or ""
        if not content:
            return False

        text_lower = content.lower()

        # Фильтры
        if any(kw in text_lower for kw in self.article_parser.IGNORE_KEYWORDS):
            return False

        is_relevant = any(k in text_lower for k in self.article_parser.DELIST_TRIGGER_KEYWORDS) or \
                      any(k.lower() in text_lower for k in self.article_parser.ST_TRIGGER_KEYWORDS)

        if not is_relevant:
            return False

        logger.info(f"[BINANCE-TG] Processing message #{message.id}: {content[:100]}...")

        pairs = self.article_parser.extract_pairs_from_text(content)
        if not pairs:
            logger.debug(f"[BINANCE-TG] No pairs found in message #{message.id}")
            return False

        event_type = DelistingEventType.DELISTING if any(k in text_lower for k in self.article_parser.DELIST_TRIGGER_KEYWORDS) else DelistingEventType.ST

        events_added = False
        for symbol in pairs:
            # Проверка на дубликаты
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
                    announcement_title=content[:200],
                    announcement_url=f"https://t.me/binance_announcements/{message.id}",
                    type=event_type
                )
                session.add(event)
                events_added = True
                event_label = "delisting" if event_type == DelistingEventType.DELISTING else "ST/Monitoring Tag"
                logger.info(f"[BINANCE-TG] New {event_label}: {symbol}")

        if events_added:
            await session.commit()
            return True

        return False
