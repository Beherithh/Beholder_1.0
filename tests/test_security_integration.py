"""
Интеграционные тесты взаимодействия модулей (Integration Tests).

Проверяют сквозное взаимодействие компонентов:
SecurityService <---> ConfigService <---> SQLite Database (AppSettings).

Ключевые проверки:
1. Запись секретов в БД: в самой таблице SQLite значение лежит строго в зашифрованном виде (Fernet token).
2. Чтение через ConfigService: ConfigService прозрачно дешифрует значение для бизнес-логики.
3. Обратная совместимость: если в БД уже лежит старый незашифрованный токен (legacy),
   ConfigService корректно отдаёт его без ошибок.
4. Обработка отсутствующих значений и пустых строк (None).
"""

import pytest
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AppSettings
from services.config import ConfigService
from services.security import SecurityService


@pytest.fixture(autouse=True)
def isolate_security_sandbox(tmp_path: Path):
    """Изолирует мастер-ключ во временной папке на время тестов."""
    test_key_file = tmp_path / "config" / "secret.key"
    test_legacy_file = tmp_path / "secret.key"

    SecurityService.set_test_paths(
        key_file=test_key_file,
        legacy_key_file=test_legacy_file,
    )
    yield
    SecurityService.set_test_paths(None, None)


@pytest.mark.asyncio
class TestSecurityConfigIntegration:
    """Интеграционные тесты связи SecurityService + ConfigService + SQLite."""

    async def test_encrypted_storage_and_transparent_retrieval(
        self,
        db_session: AsyncSession,
        config_service: ConfigService,
    ) -> None:
        """
        Сквозной тест:
        1. Шифруем Telegram Bot Token и сохраняем в AppSettings в SQLite.
        2. Убеждаемся, что в сырой таблице SQLite лежит зашифрованная строка (не открытый токен).
        3. Запрашиваем через config_service.get_telegram_config().
        4. Убеждаемся, что наружу отдаётся расшифрованный токен.
        """
        raw_token = "987654321:AAE_TestTelegramBotToken_SecretKey"
        encrypted_token = SecurityService.encrypt(raw_token)

        # Сохраняем в базу данных SQLite
        setting_entry = AppSettings(key="tg_bot_token", value=encrypted_token)
        db_session.add(setting_entry)
        await db_session.commit()

        # 1. Проверяем сырое состояние в SQLite
        raw_db_entry = await db_session.get(AppSettings, "tg_bot_token")
        assert raw_db_entry is not None
        assert raw_db_entry.value != raw_token, "В базе данных не должно быть открытого текста!"
        assert raw_db_entry.value.startswith("gAAAAA"), "Значение в БД должно быть Fernet токеном"

        # 2. Проверяем получение через ConfigService
        tg_config = await config_service.get_telegram_config()
        assert tg_config.bot_token == raw_token, "ConfigService обязан вернуть расшифрованный токен"

    async def test_cmc_api_key_encryption_and_retrieval(
        self,
        db_session: AsyncSession,
        config_service: ConfigService,
    ) -> None:
        """
        Тестирует шифрование ключа CoinMarketCap:
        Проверяет сохранение зашифрованного api_key и чтение через get_cmc_config().
        """
        raw_cmc_key = "cmc-prod-api-key-998877-uuid"
        encrypted_key = SecurityService.encrypt(raw_cmc_key)

        db_session.add(AppSettings(key="cmc_api_key", value=encrypted_key))
        await db_session.commit()

        cmc_config = await config_service.get_cmc_config()
        assert cmc_config.api_key == raw_cmc_key

    async def test_legacy_unencrypted_database_compatibility(
        self,
        db_session: AsyncSession,
        config_service: ConfigService,
    ) -> None:
        """
        Тест обратной совместимости с существующими БД пользователей:
        Если в базе осталась старая незашифрованная запись, система не должна ломаться.
        """
        plain_legacy_token = "old_unencrypted_chat_id_100200300"

        db_session.add(AppSettings(key="tg_chat_id", value=plain_legacy_token))
        await db_session.commit()

        tg_config = await config_service.get_telegram_config()
        assert tg_config.chat_id == plain_legacy_token

    async def test_empty_and_none_secrets_handling(
        self,
        db_session: AsyncSession,
        config_service: ConfigService,
    ) -> None:
        """
        Проверка отсутствующих секретов в базе данных:
        Должен корректно возвращаться None без исключений.
        """
        tg_config = await config_service.get_telegram_config()
        assert tg_config.bot_token is None
        assert tg_config.chat_id is None
        assert tg_config.api_id is None
        assert tg_config.api_hash is None
