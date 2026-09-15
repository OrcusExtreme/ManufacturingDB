@echo off
setlocal
rem This script lives in tools\ but the exe sits in the project root.
cd /d "%~dp0.."

echo ====================================================
echo   Orcus System Controller Launcher
echo ====================================================

if not exist "system_controller.exe" (
    echo [INFO] system_controller.exe not found. Compiling first...
    call "tools\compile_controller.bat"
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to compile system_controller.exe
        pause
        exit /b %ERRORLEVEL%
    )
)

echo [INFO] Launching system_controller.exe...
start "" "system_controller.exe"
