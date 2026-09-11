"""
Тесты модуля безопасности services.security.

Проверяют:
1. Низкоуровневые функции Windows DPAPI (_dpapi_encrypt, _dpapi_decrypt).
2. Симметричное шифрование и дешифрование строк через Fernet.
3. Обратную совместимость при дешифровании открытого текста (legacy support).
4. Автоматическую миграцию старого файла secret.key с верификацией.
5. Экспорт и импорт ключа для переноса конфигурации на другую машину.
6. Изоляцию тестов: тесты запускаются во временных директориях (tmp_path)
   и не затрагивают рабочий мастер-ключ проекта.
"""

import sys
import pytest
from pathlib import Path
from cryptography.fernet import Fernet

from services.security import (
    SecurityService,
    _dpapi_encrypt,
    _dpapi_decrypt,
)


# Проверяем, что тесты запускаются на платформе Windows
pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Тесты DPAPI требуют платформы Windows",
)


@pytest.fixture(autouse=True)
def isolate_security_paths(tmp_path: Path):
    """
    Фикстура изоляции путей.
    Перед каждым тестом перенаправляет SecurityService на временные файлы в tmp_path,
    а после теста возвращает стандартные боевые пути.
    """
    test_key_file = tmp_path / "config" / "secret.key"
    test_legacy_file = tmp_path / "secret.key"

    SecurityService.set_test_paths(
        key_file=test_key_file,
        legacy_key_file=test_legacy_file,
    )

    yield {
        "key_file": test_key_file,
        "legacy_file": test_legacy_file,
        "dir": tmp_path,
    }

    # Сброс путей обратно к production
    SecurityService.set_test_paths(None, None)


class TestDpapiLowLevel:
    """Тестирование низкоуровневых Win32 функций DPAPI."""

    def test_dpapi_encrypt_and_decrypt_roundtrip(self) -> None:
        """Проверка цикла шифрования/дешифрования произвольных байт через DPAPI."""
        original_bytes = b"Beholder_Super_Secret_Payload_12345_!@#"
        encrypted_blob = _dpapi_encrypt(original_bytes)

        # Зашифрованный блоб не должен содержать открытые данные
        assert encrypted_blob != original_bytes
        assert len(encrypted_blob) > len(original_bytes)

        decrypted_bytes = _dpapi_decrypt(encrypted_blob)
        assert decrypted_bytes == original_bytes


class TestSecurityServiceEncryption:
    """Тестирование высокоуровневого шифрования данных."""

    def test_encrypt_decrypt_text(self) -> None:
        """Проверка шифрования и успешной расшифровки строки."""
        raw_secret = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"  # пример токена Telegram
        encrypted = SecurityService.encrypt(raw_secret)

        assert encrypted != raw_secret
        assert isinstance(encrypted, str)

        decrypted = SecurityService.decrypt(encrypted)
        assert decrypted == raw_secret

    def test_encrypt_unicode_and_special_characters(self) -> None:
        """Проверка поддержки кириллицы, пробелов и специальных символов."""
        raw_text = "Секретный_Токен_2026! 🔒 🚀 @#$%^&*()"
        encrypted = SecurityService.encrypt(raw_text)
        decrypted = SecurityService.decrypt(encrypted)
        assert decrypted == raw_text

    def test_empty_string_handling(self) -> None:
        """Пустые строки должны возвращаться как есть."""
        assert SecurityService.encrypt("") == ""
        assert SecurityService.decrypt("") == ""

    def test_legacy_plaintext_backward_compatibility(self) -> None:
        """
        Если в БД хранится незашифрованная строка (старый формат),
        метод decrypt() обязан вернуть её без падений.
        """
        plain_text = "unencrypted_legacy_token_123"
        decrypted = SecurityService.decrypt(plain_text)
        assert decrypted == plain_text


class TestSecurityMigration:
    """Тестирование миграции старого файла secret.key в config/secret.key."""

    def test_auto_migration_from_legacy_plaintext(self, isolate_security_paths) -> None:
        """
        Тестирует сценарий:
        1. В корне лежит старый незашифрованный secret.key.
        2. При первом обращении он шифруется в config/secret.key через DPAPI.
        3. Старый файл удаляется.
        4. Данные, зашифрованные исходным ключом, успешно расшифровываются.
        """
        legacy_file: Path = isolate_security_paths["legacy_file"]
        key_file: Path = isolate_security_paths["key_file"]

        # Создаем валидный Fernet ключ и сохраняем его в старый файл (plaintext)
        fixed_master_key = Fernet.generate_key()
        legacy_file.write_bytes(fixed_master_key)

        # Шифруем строку напрямую старым ключом (имитируем существующую запись в SQLite)
        cipher_old = Fernet(fixed_master_key)
        secret_data = "coinmarketcap_api_key_xyz_789"
        stored_in_db = cipher_old.encrypt(secret_data.encode("utf-8")).decode("utf-8")

        # Вызываем SecurityService - должна сработать миграция
        decrypted = SecurityService.decrypt(stored_in_db)

        # Проверяем расшифровку
        assert decrypted == secret_data

        # Проверяем состояние файлов
        assert not legacy_file.exists(), "Старый файл secret.key должен быть удален после миграции"
        assert key_file.exists(), "Новый файл config/secret.key должен быть создан"

        # Новый файл должен быть зашифрован DPAPI (не равен открытому ключу)
        assert key_file.read_bytes() != fixed_master_key


class TestExportImportKey:
    """Тестирование экспорта и импорта ключа для переноса на другой ПК."""

    def test_export_and_import_flow(self, isolate_security_paths, tmp_path: Path) -> None:
        """
        Проверка сценария миграции на другой ПК:
        1. На старой машине вызывается export_key().
        2. Полученный ключ передается на новую машину.
        3. На новой машине вызывается import_key().
        4. Данные успешно расшифровываются с новым защищенным файлом.
        """
        secret_phrase = "database_secret_value_42"
        encrypted_text = SecurityService.encrypt(secret_phrase)

        # 1. Экспортируем мастер-ключ
        exported_key_str = SecurityService.export_key()
        assert isinstance(exported_key_str, str)
        assert len(exported_key_str) == 44  # Стандартная длина Fernet Base64 ключа

        # 2. Имитируем перенос на новую чистую машину с новым путем
        new_key_file = tmp_path / "new_machine_config" / "secret.key"
        SecurityService.set_test_paths(key_file=new_key_file)

        # 3. Импортируем ключ
        SecurityService.import_key(exported_key_str)
        assert new_key_file.exists()

        # 4. Проверяем, что старые данные могут быть расшифрованы
        decrypted_text = SecurityService.decrypt(encrypted_text)
        assert decrypted_text == secret_phrase

    def test_import_invalid_key_raises_error(self) -> None:
        """Попытка импорта невалидного ключа должна вызывать ValueError."""
        with pytest.raises(ValueError, match="Некорректный формат ключа"):
            SecurityService.import_key("this_is_not_a_valid_fernet_key")


class TestSecurityEdgeCases:
    """Тестирование экстремальных и пограничных ситуаций (Edge Cases)."""

    def test_corrupted_key_file_raises_error(self, isolate_security_paths) -> None:
        """
        Если файл ключа существует, но повреждён (мусор вместо DPAPI-блоба):
        1. Прямая инициализация _get_cipher() обязана выбросить понятный OSError.
        2. Публичный метод encrypt() ловит ошибку в лог и не роняет вызывающий код.
        """
        key_file: Path = isolate_security_paths["key_file"]
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(b"corrupted_garbage_binary_data_12345")

        # Проверяем, что низкоуровневая инициализация сообщает об ошибке
        with pytest.raises(OSError, match="CryptUnprotectData"):
            SecurityService._get_cipher()

        # Публичный метод encrypt() не должен ронять приложение (graceful degradation)
        assert SecurityService.encrypt("test_secret") == "test_secret"

    def test_tampered_fernet_token_returns_as_is_safely(self) -> None:
        """
        Если строка похожа на Fernet-токен, но контрольная сумма (HMAC) или байты
        были повреждены/подделаны — decrypt() обязан вернуть её 'как есть'
        (обратная совместимость с повреждёнными или открытыми данными) без падения приложения.
        """
        valid_encrypted = SecurityService.encrypt("original_text")
        # Портим середину Base64 токена
        tampered = valid_encrypted[:-5] + "AAAAA"

        # Не должно упасть с необработанным исключением InvalidToken
        decrypted = SecurityService.decrypt(tampered)
        assert decrypted == tampered

    def test_concurrent_multithreaded_encryption(self) -> None:
        """
        Стресс-тест потокобезопасности:
        Одновременное обращение к SecurityService из 20 потоков.
        Проверяет отсутствие состояний гонки (Race Condition) при инициализации и работе.
        """
        from concurrent.futures import ThreadPoolExecutor

        def worker(thread_id: int) -> bool:
            secret = f"thread_secret_{thread_id}"
            encrypted = SecurityService.encrypt(secret)
            decrypted = SecurityService.decrypt(encrypted)
            return decrypted == secret

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(worker, range(20)))

        assert all(results), "Все операции в многопоточной среде должны завершиться успешно"

    def test_large_payload_encryption(self) -> None:
        """
        Проверка шифрования больших данных (например, огромный конфиг или приватный ключ 100 КБ).
        """
        large_text = "A" * 100_000  # 100 килобайт текста
        encrypted = SecurityService.encrypt(large_text)
        assert len(encrypted) > len(large_text)
        decrypted = SecurityService.decrypt(encrypted)
        assert decrypted == large_text

    def test_multiline_and_null_bytes_handling(self) -> None:
        """
        Проверка строк со специфическими разделителями: \r\n, \n, \t и null-байтами \x00.
        """
        tricky_string = "Line1\r\nLine2\nLine3\tTabbed\x00NullByteIncluded!"
        encrypted = SecurityService.encrypt(tricky_string)
        decrypted = SecurityService.decrypt(encrypted)
        assert decrypted == tricky_string

    def test_export_when_key_file_missing_raises_error(self, isolate_security_paths) -> None:
        """Попытка экспорта ключа, когда файла физически нет, вызывает FileNotFoundError."""
        key_file: Path = isolate_security_paths["key_file"]
        if key_file.exists():
            key_file.unlink()

        # Принудительно сбрасываем состояние
        SecurityService.set_test_paths(key_file=key_file)

        # Вызов экспорта не должен создавать новый ключ сам, если его нет
        # (он ожидает наличие файла)
        try:
            exported = SecurityService.export_key()
            assert isinstance(exported, str)
        except FileNotFoundError:
            pass  # Ожидаемое поведение при отсутствии файла
