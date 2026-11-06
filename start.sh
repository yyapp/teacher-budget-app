#!/usr/bin/env bash
# macOS / Linux용 실행 스크립트: 처음에는 필요한 프로그램을 설치하고, 이후에는 바로 앱을 켭니다.
# 사용법: 이 폴더에서  bash start.sh
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python이 설치되어 있지 않습니다. https://www.python.org/downloads/ 에서 3.10 이상을 설치해 주세요."
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 이상이 필요합니다. (현재: $("$PY" --version 2>&1))"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "처음 실행입니다. 필요한 프로그램을 설치합니다 (1~2분)..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f .env ]; then
  echo "알림: .env 파일이 없어 영수증 자동 인식은 꺼져 있습니다. (수기 입력 등은 그대로 쓸 수 있습니다. 설정 방법은 README.md 참고)"
fi

export FLASK_PORT="${FLASK_PORT:-5002}"
URL="http://127.0.0.1:${FLASK_PORT}"
echo ""
echo "접속 주소: ${URL}"
echo "끄려면 이 창에서 Ctrl+C 를 누르세요. (실행 중에는 다른 키를 누르지 마세요)"
echo ""

# 2초 뒤 브라우저를 자동으로 연다 (열리지 않으면 위 주소를 직접 입력하세요)
( sleep 2
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1
  fi ) &

exec python app.py
