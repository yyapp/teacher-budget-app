@echo off
rem Windows용 실행 스크립트: 처음에는 필요한 프로그램을 설치하고, 이후에는 바로 앱을 켭니다.
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python이 설치되어 있지 않습니다. https://www.python.org/downloads/ 에서 3.10 이상을 설치해 주세요.
  echo 설치할 때 "Add python.exe to PATH"를 꼭 체크하세요.
  pause
  exit /b 1
)

if not exist .venv (
  echo 처음 실행입니다. 필요한 프로그램을 설치합니다 (1~2분)...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt

if not exist .env echo 알림: .env 파일이 없어 영수증 자동 인식은 꺼져 있습니다. (수기 입력 등은 그대로 쓸 수 있습니다)

if "%FLASK_PORT%"=="" set FLASK_PORT=5002
echo.
echo 접속 주소: http://127.0.0.1:%FLASK_PORT%
echo 끄려면 이 창에서 Ctrl+C 를 누르세요. (실행 중에는 다른 키를 누르지 마세요)
echo.
start "" http://127.0.0.1:%FLASK_PORT%
python app.py
pause
