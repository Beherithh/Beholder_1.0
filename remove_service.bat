@echo off
chcp 1251 >nul
setlocal EnableDelayedExpansion

echo ========================================
echo    Beholder - Delete Windows Service
echo ========================================
echo.

:: ============================================
:: Check admin rights
:: ============================================
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Administrator rights required!
    echo Run this script as Administrator.
    pause
    exit /b 1
)

:: ============================================
:: Check NSSM availability
:: ============================================
echo [1/2] Checking NSSM...

where nssm >nul 2>&1
if %ERRORLEVEL% neq 0 (
    if exist "%~dp0nssm.exe" (
        set "NSSM=%~dp0nssm.exe"
        echo [OK] Found local nssm.exe
    ) else (
        echo [ERROR] NSSM not found!
        echo.
        echo Download NSSM from https://nssm.cc/download
        echo and place nssm.exe in this folder
        pause
        exit /b 1
    )
) else (
    set "NSSM=nssm"
    echo [OK] NSSM found in PATH
)

:: ============================================
:: Setup variables
:: ============================================
set "SERVICE_NAME=Beholder"

:: ============================================
:: Check if service exists
:: ============================================
echo.
echo [2/2] Checking service...

%NSSM% status %SERVICE_NAME% >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Service %SERVICE_NAME% not found
    echo No deletion required.
    goto :end
)

:: ============================================
:: Stop service
:: ============================================
echo Stopping service %SERVICE_NAME%...
%NSSM% stop %SERVICE_NAME% >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] Service stopped
) else (
    echo [WARNING] Failed to stop service (may already be stopped)
)

:: ============================================
:: Remove service
:: ============================================
echo Removing service %SERVICE_NAME%...
%NSSM% remove %SERVICE_NAME% confirm
if %ERRORLEVEL% equ 0 (
    echo [OK] Service removed
) else (
    echo [ERROR] Failed to remove service
    pause
    exit /b 1
)

:end
:: ============================================
:: Finish
:: ============================================
echo.
echo ========================================
echo    Service deletion completed!
echo ========================================
echo.
echo Service %SERVICE_NAME% completely removed from system.
echo Logs in logs\ folder left for analysis.
echo.
pause
