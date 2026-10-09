@echo off
setlocal
rem Navigate to project root
cd /d "%~dp0.."

echo ========================================================
echo   Manufacturing DB REST API Server
echo ========================================================

where py.exe >nul 2>&1
if %ERRORLEVEL% equ 0 (
    py -3 "%~dp0run_api.py"
) else (
    python "%~dp0run_api.py"
)

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to start API server.
)

pause
