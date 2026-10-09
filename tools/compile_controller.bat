@echo off
setlocal
rem This script lives in tools\ but every path below is relative to the project root.
cd /d "%~dp0.."

rem Optional first argument: output exe path (absolute, or relative to the project root).
rem The installer build uses it to compile into its staging folder, so a controller
rem that is running from the project root does not block the build.
set "OUT_EXE=%~1"
if "%OUT_EXE%"=="" set "OUT_EXE=system_controller.exe"

echo ====================================================
echo   Building Orcus System Controller
echo ====================================================

if exist "D:\tools\w64devkit\bin" (
    set "PATH=D:\tools\w64devkit\bin;%PATH%"
)

where g++.exe >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] g++ compiler not found.
    echo         Install w64devkit or add MinGW-w64 to PATH.
    exit /b 1
)

if not exist "build" mkdir "build"

rem -- Icon / version resource (skipped if windres is unavailable) --
set "RES_OBJ="
where windres.exe >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [1/2] Compiling resources ^(icon, version info^)...
    windres.exe "controller\system_controller.rc" -O coff -o "build\resources.o"
    if %ERRORLEVEL% equ 0 (
        set "RES_OBJ=build\resources.o"
    ) else (
        echo [WARN] Resource compile failed. Continuing without the embedded icon.
    )
) else (
    echo [WARN] windres not found. Continuing without the embedded icon.
)

echo [2/2] Compiling C++ Win32 application...
g++.exe -std=c++17 -O2 -municode -mwindows ^
    "controller\system_controller.cpp" %RES_OBJ% ^
    -o "%OUT_EXE%" ^
    -lcomctl32 -lshlwapi -lgdiplus -lgdi32 -luser32 -lole32 -liphlpapi ^
    -static -static-libgcc -static-libstdc++

if %ERRORLEVEL% equ 0 (
    echo.
    echo [SUCCESS] %OUT_EXE%
    echo ====================================================
) else (
    echo.
    echo [FAILED] Compilation error.
    echo ====================================================
    exit /b %ERRORLEVEL%
)
