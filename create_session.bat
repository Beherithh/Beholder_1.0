@echo off
chcp 65001 > nul
echo Запуск мастера создания сессии Telegram...

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo Виртуальное окружение .venv не найдено. Пробую запустить через системный python...
)

python create_session.py

echo.
echo Нажмите любую клавишу для выхода...
pause > nul
