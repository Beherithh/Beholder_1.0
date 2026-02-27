from services.security import SecurityService
import os

def test():
    print("--- Тест безопасности ---")
    
    # 1. Проверка файла ключа
    key_file = "secret.key"
    if os.path.exists(key_file):
        print(f"[OK] Файл {key_file} существует.")
        with open(key_file, 'rb') as f:
            print(f"     Размер ключа: {len(f.read())} байт")
    else:
        print(f"[INFO] Файл {key_file} не найден. Он должен быть создан при первом шифровании.")

    # 2. Тест шифрования
    original = "MySecretPassword123"
    print(f"\nПопытка зашифровать: '{original}'")
    
    encrypted = SecurityService.encrypt(original)
    
    if encrypted == original:
        print("[ОШИБКА] Строка НЕ зашифрована! Функция вернула исходный текст.")
        print("Проверьте логи (stdout/stderr) на наличие ошибок от loguru.")
    else:
        print(f"[УСПЕХ] Зашифровано: {encrypted}")
        
        # 3. Тест дешифрования
        decrypted = SecurityService.decrypt(encrypted)
        print(f"Расшифровано: {decrypted}")
        
        if decrypted == original:
            print("[УСПЕХ] Цикл шифрования/дешифрования работает корректно.")
        else:
            print("[ОШИБКА] Расшифрованная строка не совпадает с оригиналом!")

if __name__ == "__main__":
    test()
