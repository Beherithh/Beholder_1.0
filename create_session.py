import asyncio
import os
from pyrogram import Client

async def create_session():
    print("=== Мастер создания сессии Telegram для Beholder ===")
    print("Этот скрипт создаст файл 'beholder_telegram.session', необходимый для чтения Телеграмм каналов.")
    
    api_id = input("Введите API ID: ").strip()
    api_hash = input("Введите API Hash: ").strip()
    
    if not api_id or not api_hash:
        print("Ошибка: API ID и Hash обязательны.")
        return

    print(f"\nПодключение к Telegram...")
    
    # Удаляем старую сессию, если есть
    if os.path.exists("beholder_telegram.session"):
        os.remove("beholder_telegram.session")

    app = Client("beholder_telegram", api_id=int(api_id), api_hash=api_hash)

    try:
        await app.start()
        me = await app.get_me()
        print(f"\nУСПЕХ! Сессия создана для пользователя: {me.first_name} (@{me.username})")
        print("Файл 'beholder_telegram.session' создан в текущей папке.")
        print("Теперь вы можете перезапустить Beholder, и он подхватит эту сессию.")
        await app.stop()
    except Exception as e:
        print(f"\nОШИБКА: {e}")

if __name__ == "__main__":
    asyncio.run(create_session())
