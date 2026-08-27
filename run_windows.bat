@echo off
chcp 65001 >nul
echo ====================================================
echo   공작기계지능화실험실 시스템 시작 (Windows)
echo ====================================================

:: Check for python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [에러] Python이 설치되어 있지 않거나 환경 변수(PATH)에 추가되어 있지 않습니다.
    echo Python 웹사이트에서 다운로드하여 설치할 때 "Add Python to PATH"를 체크해 주세요.
    pause
    exit /b 1
)

:: Create virtual environment if it doesn't exist
IF NOT EXIST "venv\Scripts\activate.bat" (
    echo [정보] 가상 환경(venv)을 생성합니다...
    python -m venv venv
)

:: Activate virtual environment
call venv\Scripts\activate.bat

:: Upgrade pip and install requirements
echo [정보] 패키지 의존성을 확인 및 설치합니다...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt

:: Run the system
echo [정보] 통합 파이프라인(run_system.py)을 실행합니다...
python run_system.py

pause
