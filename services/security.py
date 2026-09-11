"""
Модуль безопасности Beholder.

Обеспечивает шифрование и дешифрование конфиденциальных данных
(токенов Telegram, API-ключей CoinMarketCap и др.) с помощью Fernet (AES-128-CBC + HMAC-SHA256).

Мастер-ключ Fernet хранится в файле config/secret.key и защищён
Windows DPAPI (Data Protection API) на уровне машины — даже при физической краже
файла расшифровать его на другом компьютере невозможно.
"""

import os
import sys
import threading
import ctypes
import ctypes.wintypes as wintypes
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from loguru import logger


# ---------------------------------------------------------------------------
# Пути к файлам по умолчанию
# ---------------------------------------------------------------------------
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
_CONFIG_DIR: Path = _PROJECT_ROOT / "config"
_DEFAULT_KEY_FILE: Path = _CONFIG_DIR / "secret.key"
_DEFAULT_LEGACY_KEY_FILE: Path = _PROJECT_ROOT / "secret.key"


# ---------------------------------------------------------------------------
# Windows DPAPI через ctypes
# ---------------------------------------------------------------------------

class _DATA_BLOB(ctypes.Structure):
    """
    Структура Win32 DATA_BLOB.
    Используется функциями CryptProtectData / CryptUnprotectData.
    """
    _fields_ = [
        ("cbData", wintypes.DWORD),                 # Размер буфера данных в байтах
        ("pbData", ctypes.POINTER(ctypes.c_byte)),  # Указатель на массив байт
    ]


# Флаг привязки DPAPI к машине (а не к пользователю).
# Это критично, так как Beholder может работать как Windows-служба от SYSTEM,
# либо запускаться пользователем вручную.
_CRYPTPROTECT_LOCAL_MACHINE: int = 0x04

# Инициализация Win32 DLL с явным указанием сигнатур (argtypes/restype).
# На 64-битных ОС отсутствие явных сигнатур в ctypes приводит к обрезке указателей.
if sys.platform == "win32":
    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),  # pDataIn
        wintypes.LPCWSTR,            # szDataDescr
        ctypes.POINTER(_DATA_BLOB),  # pOptionalEntropy
        wintypes.LPVOID,             # pvReserved
        ctypes.c_void_p,             # pPromptStruct
        wintypes.DWORD,              # dwFlags
        ctypes.POINTER(_DATA_BLOB),  # pDataOut
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),      # pDataIn
        ctypes.POINTER(wintypes.LPWSTR), # ppszDataDescr
        ctypes.POINTER(_DATA_BLOB),      # pOptionalEntropy
        wintypes.LPVOID,                 # pvReserved
        ctypes.c_void_p,                 # pPromptStruct
        wintypes.DWORD,                  # dwFlags
        ctypes.POINTER(_DATA_BLOB),      # pDataOut
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL

    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL
else:
    _crypt32 = None
    _kernel32 = None


def _dpapi_encrypt(data: bytes) -> bytes:
    """
    Шифрует данные через Windows DPAPI (machine-level).

    Args:
        data: Исходные байты для шифрования.

    Returns:
        Зашифрованный бинарный блоб.

    Raises:
        OSError: Если платформа не Windows или API завершился с ошибкой.
    """
    if sys.platform != "win32" or _crypt32 is None:
        raise OSError("DPAPI доступен только на платформе Windows.")

    input_blob = _DATA_BLOB()
    input_blob.cbData = len(data)
    buffer = ctypes.create_string_buffer(data, len(data))
    input_blob.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))

    output_blob = _DATA_BLOB()

    success = _crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_LOCAL_MACHINE,
        ctypes.byref(output_blob),
    )

    if not success:
        error_code = ctypes.GetLastError()
        raise OSError(f"CryptProtectData завершилась с ошибкой Windows: {error_code}")

    encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    _kernel32.LocalFree(output_blob.pbData)
    return encrypted


def _dpapi_decrypt(data: bytes) -> bytes:
    """
    Расшифровывает бинарный блоб, созданный через DPAPI на этой машине.

    Args:
        data: Зашифрованные байты.

    Returns:
        Расшифрованные исходные байты.

    Raises:
        OSError: Если платформа не Windows или расшифровка не удалась.
    """
    if sys.platform != "win32" or _crypt32 is None:
        raise OSError("DPAPI доступен только на платформе Windows.")

    input_blob = _DATA_BLOB()
    input_blob.cbData = len(data)
    buffer = ctypes.create_string_buffer(data, len(data))
    input_blob.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))

    output_blob = _DATA_BLOB()

    success = _crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )

    if not success:
        error_code = ctypes.GetLastError()
        raise OSError(
            f"CryptUnprotectData завершилась с ошибкой Windows: {error_code}. "
            "Ключ зашифрован на другой машине или данные повреждены."
        )

    decrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    _kernel32.LocalFree(output_blob.pbData)
    return decrypted


# ---------------------------------------------------------------------------
# Сервис управления безопасностью
# ---------------------------------------------------------------------------

class SecurityService:
    """
    Потокобезопасный синглтон-сервис шифрования и дешифрования данных.

    Публичные методы:
        encrypt(text)       -> str  : Шифрует строку в Base64 Fernet токен.
        decrypt(text)       -> str  : Расшифровывает Base64 строку или возвращает как есть.
        export_key()        -> str  : Экспортирует мастер-ключ для переноса на другой ПК.
        import_key(raw_key) -> None : Импортирует мастер-ключ и защищает его DPAPI.
    """

    _cipher: Optional[Fernet] = None
    _lock: threading.Lock = threading.Lock()

    # Точки расширения путей для изоляции модульных тестов
    _override_key_file: Optional[Path] = None
    _override_legacy_key_file: Optional[Path] = None

    @classmethod
    def set_test_paths(
        cls,
        key_file: Optional[Path] = None,
        legacy_key_file: Optional[Path] = None,
    ) -> None:
        """
        Переопределяет пути к файлам ключей.
        Используется исключительно в тестах для изоляции от боевых данных.
        """
        with cls._lock:
            cls._override_key_file = key_file
            cls._override_legacy_key_file = legacy_key_file
            cls._cipher = None

    @classmethod
    def _get_key_file_path(cls) -> Path:
        """Возвращает актуальный путь к рабочему файлу ключа."""
        return cls._override_key_file or _DEFAULT_KEY_FILE

    @classmethod
    def _get_legacy_key_file_path(cls) -> Path:
        """Возвращает актуальный путь к старому файлу ключа."""
        return cls._override_legacy_key_file or _DEFAULT_LEGACY_KEY_FILE

    @classmethod
    def _get_cipher(cls) -> Fernet:
        """
        Инициализирует и возвращает Fernet-шифровальщик с двойной проверкой блокировки.
        """
        if cls._cipher is not None:
            return cls._cipher

        with cls._lock:
            if cls._cipher is not None:
                return cls._cipher

            key_file = cls._get_key_file_path()
            legacy_file = cls._get_legacy_key_file_path()

            # Гарантируем наличие родительской папки
            key_file.parent.mkdir(parents=True, exist_ok=True)

            # Миграция из старого формата (если есть старый и нет нового)
            if legacy_file.exists() and not key_file.exists():
                cls._migrate_legacy_key(legacy_file, key_file)

            # Загрузка существующего либо генерация нового
            if key_file.exists():
                key = cls._read_key(key_file)
            else:
                key = cls._generate_new_key(key_file)

            cls._cipher = Fernet(key)
            return cls._cipher

    @classmethod
    def _migrate_legacy_key(cls, legacy_file: Path, key_file: Path) -> None:
        """
        Безопасно переносит legacy plaintext ключ в DPAPI с верификацией чтения.
        Старый файл удаляется ТОЛЬКО при успешной валидации нового.
        """
        logger.info(f"Миграция мастер-ключа: {legacy_file} -> {key_file}")
        try:
            raw_key = legacy_file.read_bytes().strip()

            # Проверка, что ключ соответствует спецификации Fernet
            Fernet(raw_key)

            # Шифрование и запись
            encrypted_key = _dpapi_encrypt(raw_key)
            key_file.write_bytes(encrypted_key)

            # Верификация Read-After-Write: расшифровываем и сравниваем
            verified_key = cls._read_key(key_file)
            if verified_key != raw_key:
                key_file.unlink(missing_ok=True)
                raise ValueError("Ошибка верификации: расшифрованный ключ не совпал с оригиналом!")

            # Только после успешного подтверждения удаляем legacy-файл
            legacy_file.unlink()
            logger.info("Миграция мастер-ключа успешно завершена.")
        except Exception as e:
            logger.error(f"Сбой миграции мастер-ключа: {e}")
            raise

    @classmethod
    def _read_key(cls, key_file: Path) -> bytes:
        """Считывает и расшифровывает мастер-ключ из указанного файла."""
        encrypted_key = key_file.read_bytes()
        return _dpapi_decrypt(encrypted_key)

    @classmethod
    def _generate_new_key(cls, key_file: Path) -> bytes:
        """Генерирует новый ключ Fernet и сохраняет его под защитой DPAPI."""
        logger.warning(f"Файл ключа {key_file} не найден. Генерация нового мастер-ключа.")
        raw_key = Fernet.generate_key()
        encrypted_key = _dpapi_encrypt(raw_key)
        key_file.write_bytes(encrypted_key)
        logger.info(f"Новый мастер-ключ сохранён и защищён DPAPI в {key_file}")
        return raw_key

    @classmethod
    def encrypt(cls, text: str) -> str:
        """
        Шифрует строковые данные.

        Args:
            text: Исходная строка для шифрования.

        Returns:
            Строка в формате Fernet Base64 токена, либо пустая строка.
        """
        if not text:
            return ""
        try:
            cipher = cls._get_cipher()
            encrypted_bytes = cipher.encrypt(text.encode("utf-8"))
            return encrypted_bytes.decode("utf-8")
        except Exception as e:
            logger.error(f"Ошибка шифрования данных: {e}")
            return text

    @classmethod
    def decrypt(cls, text: str) -> str:
        """
        Расшифровывает данные с поддержкой обратной совместимости.
        Если строка не была зашифрована или повреждена, возвращает её как есть.

        Args:
            text: Зашифрованная строка.

        Returns:
            Исходная расшифрованная строка.
        """
        if not text:
            return ""
        try:
            cipher = cls._get_cipher()
            decrypted_bytes = cipher.decrypt(text.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except Exception:
            # Строка не является валидным Fernet-токеном (legacy plaintext)
            return text

    @classmethod
    def export_key(cls) -> str:
        """
        Экспортирует мастер-ключ в открытом виде для переноса на другой компьютер.

        Returns:
            Строка Base64 мастер-ключа Fernet.
        """
        cipher = cls._get_cipher()
        key_file = cls._get_key_file_path()
        raw_key = cls._read_key(key_file)
        return raw_key.decode("utf-8")

    @classmethod
    def import_key(cls, raw_key_str: str) -> None:
        """
        Импортирует мастер-ключ на новую машину и шифрует локальным DPAPI.

        Args:
            raw_key_str: Base64 строка ключа Fernet.
        """
        raw_key = raw_key_str.strip().encode("utf-8")
        try:
            Fernet(raw_key)
        except Exception as e:
            raise ValueError(f"Некорректный формат ключа Fernet: {e}")

        with cls._lock:
            key_file = cls._get_key_file_path()
            key_file.parent.mkdir(parents=True, exist_ok=True)
            encrypted_key = _dpapi_encrypt(raw_key)
            key_file.write_bytes(encrypted_key)
            cls._cipher = None

        logger.info(f"Мастер-ключ успешно импортирован в {key_file}")
