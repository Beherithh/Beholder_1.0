@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ========================================
echo    Beholder - Установка службы Windows
echo ========================================
echo.

:: ============================================
:: Проверка прав администратора
:: ============================================
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ОШИБКА] Требуются права администратора!
    echo Запустите этот скрипт от имени администратора.
    pause
    exit /b 1
)

:: ============================================
:: Проверка наличия NSSM
:: ============================================
echo [1/3] Проверка NSSM...

where nssm >nul 2>&1
if %ERRORLEVEL% neq 0 (
    if exist "%~dp0nssm.exe" (
        set "NSSM=%~dp0nssm.exe"
        echo [OK] Найден локальный nssm.exe
    ) else (
        echo [ОШИБКА] NSSM не найден!
        echo.
        echo Скачайте NSSM с https://nssm.cc/download
        echo и положите nssm.exe в папку с этим скриптом
        echo или в C:\Windows\System32
        pause
        exit /b 1
    )
) else (
    set "NSSM=nssm"
    echo [OK] NSSM найден в PATH
)

:: ============================================
:: Настройка путей
:: ============================================
set "SERVICE_NAME=Beholder"
set "APP_DIR=%~dp0"
set "PYTHON_EXE=%APP_DIR%.venv\Scripts\python.exe"
set "MAIN_SCRIPT=main.py"
set "LOG_DIR=%APP_DIR%logs"

:: Убираем trailing backslash если есть
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"

:: Проверка виртуального окружения
if not exist "%PYTHON_EXE%" (
    echo [ОШИБКА] Виртуальное окружение не найдено!
    echo Сначала выполните install.bat
    pause
    exit /b 1
)

:: ============================================
:: Удаление старой службы (если есть)
:: ============================================
echo.
echo [2/3] Проверка существующей службы...

%NSSM% status %SERVICE_NAME% >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo Служба %SERVICE_NAME% уже существует. Удаляем...
    %NSSM% stop %SERVICE_NAME% >nul 2>&1
    %NSSM% remove %SERVICE_NAME% confirm
    timeout /t 2 >nul
)

:: ============================================
:: Создание папки для логов
:: ============================================
if not exist "%LOG_DIR%" (
    mkdir "%LOG_DIR%"
)

:: ============================================
:: Установка службы
:: ============================================
echo.
echo [3/3] Установка службы %SERVICE_NAME%...

%NSSM% install %SERVICE_NAME% "%PYTHON_EXE%" "%MAIN_SCRIPT%"
if %ERRORLEVEL% neq 0 (
    echo [ОШИБКА] Не удалось установить службу
    pause
    exit /b 1
)

:: Настройка службы
%NSSM% set %SERVICE_NAME% AppDirectory "%APP_DIR%"
%NSSM% set %SERVICE_NAME% DisplayName "Beholder"
%NSSM% set %SERVICE_NAME% Description "Delisting monitor with Telegram alerts"
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START

:: Настройка логов
%NSSM% set %SERVICE_NAME% AppStdout "%LOG_DIR%\stdout.log"
%NSSM% set %SERVICE_NAME% AppStderr "%LOG_DIR%\stderr.log"
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 10485760

:: Настройка перезапуска при падении
%NSSM% set %SERVICE_NAME% AppExit Default Restart
%NSSM% set %SERVICE_NAME% AppRestartDelay 5000

echo [OK] Служба установлена

:: ============================================
:: Запуск службы
:: ============================================
echo.
set /p START_NOW="Запустить службу сейчас? (Y/N): "
if /i "%START_NOW%"=="Y" (
    %NSSM% start %SERVICE_NAME%
    if %ERRORLEVEL% equ 0 (
        echo [OK] Служба запущена
    ) else (
        echo [ОШИБКА] Не удалось запустить службу
    )
)

:: ============================================
:: Готово
:: ============================================
echo.
echo ========================================
echo    Установка службы завершена!
echo ========================================
echo.
echo Управление службой:
echo   nssm start %SERVICE_NAME%    - Запуск
echo   nssm stop %SERVICE_NAME%     - Остановка
echo   nssm restart %SERVICE_NAME%  - Перезапуск
echo   nssm status %SERVICE_NAME%   - Статус
echo   nssm remove %SERVICE_NAME%   - Удаление
echo.
echo Логи: %LOG_DIR%
echo.
pause
