@echo off
chcp 65001 >nul

echo ========================================
echo    Beholder - Запуск приложения
echo ========================================
echo.

:: Переходим в директорию скрипта
cd /d "%~dp0"

:: Проверка виртуального окружения
if not exist ".venv\Scripts\python.exe" (
    echo [ОШИБКА] Виртуальное окружение не найдено!
    echo Сначала выполните install.bat
    pause
    exit /b 1
)

:: Активация виртуального окружения и запуск
echo Запуск Beholder...
echo Веб-интерфейс: http://localhost:8080
echo.
echo Для остановки нажмите Ctrl+C
echo ----------------------------------------
echo.

call .venv\Scripts\activate.bat
python main.py

:: Если программа завершилась с ошибкой
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ОШИБКА] Программа завершилась с кодом %ERRORLEVEL%
    pause
)
