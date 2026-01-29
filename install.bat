@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ========================================
echo    Beholder - Установка окружения
echo ========================================
echo.

:: ============================================
:: 1. Проверка Python 3.12+
:: ============================================
echo [1/4] Проверка Python...

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ОШИБКА] Python не найден в системе!
    echo Скачайте Python 3.12+ с https://www.python.org/downloads/
    echo При установке обязательно поставьте галочку "Add Python to PATH"
    pause
    exit /b 1
)

:: Получаем версию Python
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYTHON_VERSION=%%v

:: Извлекаем major и minor версии
for /f "tokens=1,2 delims=." %%a in ("%PYTHON_VERSION%") do (
    set PYTHON_MAJOR=%%a
    set PYTHON_MINOR=%%b
)

:: Проверяем версию (минимум 3.12)
if %PYTHON_MAJOR% lss 3 (
    echo [ОШИБКА] Требуется Python 3.12 или выше. Найден: %PYTHON_VERSION%
    pause
    exit /b 1
)
if %PYTHON_MAJOR% equ 3 if %PYTHON_MINOR% lss 12 (
    echo [ОШИБКА] Требуется Python 3.12 или выше. Найден: %PYTHON_VERSION%
    pause
    exit /b 1
)

echo [OK] Python %PYTHON_VERSION% найден

:: ============================================
:: 2. Установка UV
:: ============================================
echo.
echo [2/4] Установка UV (менеджер пакетов)...

where uv >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo UV не найден, устанавливаем...
    pip install uv --quiet
    if %ERRORLEVEL% neq 0 (
        echo [ОШИБКА] Не удалось установить UV
        pause
        exit /b 1
    )
    echo [OK] UV установлен
) else (
    echo [OK] UV уже установлен
)

:: ============================================
:: 3. Создание виртуального окружения
:: ============================================
echo.
echo [3/4] Создание виртуального окружения...

if exist ".venv" (
    echo [OK] Виртуальное окружение уже существует
) else (
    uv venv
    if %ERRORLEVEL% neq 0 (
        echo [ОШИБКА] Не удалось создать виртуальное окружение
        pause
        exit /b 1
    )
    echo [OK] Виртуальное окружение создано
)

:: ============================================
:: 4. Установка зависимостей
:: ============================================
echo.
echo [4/4] Установка зависимостей...

:: Проверяем наличие pyproject.toml или requirements.txt
if exist "pyproject.toml" (
    echo Найден pyproject.toml, выполняем uv sync...
    uv sync
) else (
    echo [ПРЕДУПРЕЖДЕНИЕ] Не найден pyproject.toml
    echo Установка зависимостей пропущена
)

if %ERRORLEVEL% neq 0 (
    echo [ОШИБКА] Не удалось установить зависимости
    pause
    exit /b 1
)

echo [OK] Зависимости установлены

:: ============================================
:: Готово
:: ============================================
echo.
echo ========================================
echo    Установка завершена успешно!
echo ========================================
echo.
echo Для запуска приложения используйте:
echo   run.bat
echo.
echo Для установки как службу Windows:
echo   install_service.bat
echo.
pause
