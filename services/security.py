import os
from cryptography.fernet import Fernet
from loguru import logger

KEY_FILE = "secret.key"

class SecurityService:
    _cipher = None

    @classmethod
    def _get_cipher(cls):
        """
        Инициализирует шифровальщик.
        Если файл ключа не существует, создает новый.
        """
        if cls._cipher:
            return cls._cipher

        if not os.path.exists(KEY_FILE):
            logger.warning(f"Файл ключей {KEY_FILE} не найден. Генерирую новый мастер-ключ.")
            key = Fernet.generate_key()
            with open(KEY_FILE, "wb") as f:
                f.write(key)
        else:
            with open(KEY_FILE, "rb") as f:
                key = f.read()

        cls._cipher = Fernet(key)
        return cls._cipher

    @classmethod
    def encrypt(cls, text: str) -> str:
        """Шифрует строку."""
        if not text:
            return ""
        try:
            cipher = cls._get_cipher()
            # Fernet работает с байтами, возвращает байты. Мы храним строки.
            encrypted_bytes = cipher.encrypt(text.encode('utf-8'))
            return encrypted_bytes.decode('utf-8')
        except Exception as e:
            logger.error(f"Ошибка шифрования: {e}")
            return text

    @classmethod
    def decrypt(cls, text: str) -> str:
        """
        Расшифровывает строку.
        Поддерживает обратную совместимость: если строка не зашифрована, возвращает её как есть.
        """
        if not text:
            return ""
        try:
            cipher = cls._get_cipher()
            decrypted_bytes = cipher.decrypt(text.encode('utf-8'))
            return decrypted_bytes.decode('utf-8')
        except Exception:
            # Если возникла ошибка (например, InvalidToken), значит строка не зашифрована
            # или зашифрована другим ключом. Возвращаем "как есть" (Legacy support).
            return text
