import asyncio
from loguru import logger

# Важно: Скрипт должен иметь доступ к моделям и ядру БД
from database.core import get_session
from database.models import AppSettings
from services.security import SecurityService

async def main():
    """
    Основная функция для создания сессии Pyrogram.
    """
    print("--- Скрипт создания сессии Pyrogram ---")
    
    api_id = None
    api_hash = None

    # 1. Получаем учетные данные из базы данных
    try:
        async with get_session() as session:
            id_setting = await session.get(AppSettings, "tg_api_id")
            hash_setting = await session.get(AppSettings, "tg_api_hash")
            
            if id_setting and id_setting.value:
                # Расшифровываем значение
                api_id = SecurityService.decrypt(id_setting.value)
            
            if hash_setting and hash_setting.value:
                # Расшифровываем значение
                api_hash = SecurityService.decrypt(hash_setting.value)

    except Exception as e:
        print(f"\n[ОШИБКА] Не удалось подключиться к базе данных: {e}")
        input("\nНажмите Enter для выхода...")
        return

    if not api_id or not api_hash:
        print("\n[ОШИБКА] 'API ID' и 'API Hash' не найдены в настройках.")
        print("Пожалуйста, сначала сохраните их на странице настроек.")
        input("\nНажмите Enter для выхода...")
        return

    print(f"\nИспользуется API ID: {api_id[:4]}...")

    # 2. Запускаем клиент Pyrogram
    try:
        from pyrogram import Client
        
        # Имя сессии 'beholder_telegram' должно совпадать с тем, что в telegram_monitor.py
        app = Client("beholder_telegram", api_id=int(api_id), api_hash=api_hash, workdir=".")
        
        async with app:
            me = await app.get_me()
            print(f"\n[УСПЕХ] Сессия успешно создана для пользователя: @{me.username}")
            print("Файл 'beholder_telegram.session' сохранен в корневой папке проекта.")
            
    except Exception as e:
        print(f"\n[ОШИБКА] Произошла ошибка во время создания сессии: {e}")
        print("Возможные причины:")
        print("- Неправильный API ID или API Hash.")
        print("- Неверно введен код подтверждения или пароль 2FA.")
        
    finally:
        input("\nНажмите Enter, чтобы закрыть это окно...")

if __name__ == "__main__":
    # Запускаем асинхронную функцию
    asyncio.run(main())
