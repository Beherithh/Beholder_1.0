import httpx
import asyncio
from typing import Optional
from loguru import logger

class TelegramService:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{token}" if token else ""

    def update_config(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{token}"

    async def send_message(self, text: str) -> bool:
        """
        Отправляет текстовое сообщение в Telegram.
        """
        if not self.token or not self.chat_id:
            logger.warning("Telegram Bot Token или Chat ID не настроены.")
            return False

        url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()
                if result.get("ok"):
                    return True
                else:
                    logger.error(f"Ошибка Telegram API: {result}")
                    return False
        except Exception as e:
            logger.error(f"Исключение при отправке в Telegram: {e}")
            return False

    async def test_connection(self) -> bool:
        """
        Отправляет тестовое сообщение для проверки настроек.
        """
        test_msg = "<b>Beholder Bot</b>\n\n✅ Связь установлена! Бот готов к отправке уведомлений."
        return await self.send_message(test_msg)
