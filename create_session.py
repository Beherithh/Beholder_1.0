import asyncio
import os

# Хак для Python 3.10+ — event loop должен существовать ДО импорта Pyrogram
try:
    asyncio.get_running_loop()
except RuntimeError:
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
)
from database.core import get_session
from database.models import AppSettings
from services.security import SecurityService

# --- Константы ---
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_NAME = "beholder_telegram"
SESSION_PATH = os.path.join(PROJECT_DIR, SESSION_NAME)
SESSION_FILE = SESSION_PATH + ".session"


async def get_config_from_db() -> tuple[str | None, str | None]:
    """Считывает и расшифровывает API ID и Hash из базы данных."""
    async with get_session() as session:
        api_id_obj = await session.get(AppSettings, "tg_api_id")
        api_hash_obj = await session.get(AppSettings, "tg_api_hash")
        api_id = SecurityService.decrypt(api_id_obj.value) if api_id_obj else None
        api_hash = SecurityService.decrypt(api_hash_obj.value) if api_hash_obj else None
        return api_id, api_hash


async def create_session() -> None:
    """
    Мастер создания сессии Telegram с ручным управлением аутентификацией.
    Поддерживает:
    - Получение кода через Telegram app
    - Переотправку кода через SMS
    - Двухфакторную аутентификацию (2FA / Cloud Password)
    """
    print("=" * 55)
    print("  Мастер создания сессии Telegram для Beholder")
    print("=" * 55)
    print(f"\n📁 Файл сессии: {SESSION_FILE}\n")

    # --- Шаг 1: API credentials ---
    print("Шаг 1: Чтение настроек из базы данных...")
    try:
        db_api_id, db_api_hash = await get_config_from_db()
    except Exception as e:
        print(f"  ⚠️  Ошибка чтения БД: {e}")
        db_api_id, db_api_hash = None, None

    if db_api_id and db_api_hash:
        print(f"  ✅ API ID найден в БД: {db_api_id}")
        api_id = db_api_id
        api_hash = db_api_hash
    else:
        print("  ℹ️  Настройки не найдены в БД. Введите вручную.")
        api_id = input("  Введите API ID: ").strip()
        api_hash = input("  Введите API Hash: ").strip()

    if not api_id or not api_hash:
        print("\n❌ Ошибка: API ID и Hash обязательны.")
        return

    # --- Шаг 2: Удалить старый файл сессии ---
    if os.path.exists(SESSION_FILE):
        try:
            os.remove(SESSION_FILE)
            print(f"🗑️  Старый файл сессии удалён.")
        except PermissionError:
            print("❌ Файл сессии занят. Закройте Beholder и попробуйте снова.")
            return

    # --- Шаг 3: Создать клиент (без автоматического auth) ---
    app = Client(
        SESSION_PATH,
        api_id=int(api_id),
        api_hash=api_hash,
        ipv6=False,
        app_version="Beholder 1.0",
        device_model="PC",
        system_version="Windows",
        # no_updates=True не нужен при ручном auth
    )

    # --- Шаг 4: Подключиться без авторизации ---
    print("\nШаг 2: Подключение к серверам Telegram...")
    await app.connect()
    print("  ✅ Соединение установлено.")

    # --- Шаг 5: Ввод номера телефона ---
    phone = input("\nШаг 3: Введите номер телефона (с кодом страны): ").strip()

    # --- Шаг 6: Отправить запрос кода ---
    print("\nШаг 4: Запрос кода подтверждения...")
    try:
        sent = await app.send_code(phone)
    except PhoneNumberInvalid:
        print("❌ Неверный формат номера телефона.")
        await app.disconnect()
        return
    except FloodWait as e:
        print(f"❌ FloodWait: подождите {e.value} секунд ({e.value // 60} мин) и попробуйте снова.")
        await app.disconnect()
        return

    print(f"  ✅ Код отправлен. Метод доставки: {sent.type}")

    # --- Шаг 7: Предложить переслать через SMS ---
    resend = input("\n  Код пришёл в Telegram? Если НЕТ — напишите 'sms' для переотправки по SMS. Иначе нажмите Enter: ").strip().lower()

    if resend == "sms":
        print("  ♻️  Запрашиваем код через SMS...")
        try:
            sent = await app.resend_code(phone, sent.phone_code_hash)
            print(f"  ✅ SMS отправлена. Новый метод доставки: {sent.type}")
        except FloodWait as e:
            print(f"  ❌ FloodWait: подождите {e.value} сек и попробуйте снова.")
            await app.disconnect()
            return
        except Exception as e:
            print(f"  ⚠️  Не удалось переотправить: {e}")

    # --- Шаг 8: Ввод кода ---
    code = input("\nШаг 5: Введите полученный код подтверждения: ").strip()

    # --- Шаг 9: Войти ---
    try:
        await app.sign_in(phone, sent.phone_code_hash, code)
        print("\n✅ Авторизация успешна!")

    except PhoneCodeInvalid:
        print("❌ Неверный код. Запустите скрипт заново.")
        await app.disconnect()
        return

    except PhoneCodeExpired:
        print("❌ Код устарел. Запустите скрипт заново — придёт новый код.")
        await app.disconnect()
        return

    except SessionPasswordNeeded:
        # --- Двухфакторная аутентификация ---
        print("\n🔐 Включена двухфакторная аутентификация (2FA).")
        password = input("  Введите ваш Cloud Password: ").strip()
        try:
            await app.check_password(password)
            print("  ✅ 2FA пройдена успешно!")
        except Exception as e:
            print(f"  ❌ Неверный пароль 2FA: {e}")
            await app.disconnect()
            return

    # --- Шаг 10: Получить данные и сохранить сессию ---
    me = await app.get_me()
    print(f"\n🎉 УСПЕХ! Сессия создана для: {me.first_name} (@{me.username})")
    print(f"   Файл сохранён: {SESSION_FILE}")

    await app.disconnect()


if __name__ == "__main__":
    asyncio.run(create_session())
