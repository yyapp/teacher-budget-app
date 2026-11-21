"""Flask 앱 진입점."""

import os
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, send_file, session

# .env 파일 로드 (로컬 개발용)
load_dotenv()

from supabase_app.routes import blueprint
from supabase_app.db import reset_client


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/__preview-bridge.js")
    def preview_bridge():
        bridge = Path(__file__).resolve().parent.parent / "__preview-bridge.js"
        return send_file(bridge, mimetype="text/javascript")

    # 세션 서명에 예측 불가능한 키를 사용한다.
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

    # 세션 쿠키 설정
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # 라우트 등록
    app.register_blueprint(blueprint)

    # 초기화: 테이블 존재 확인 (Supabase 콘솔에서 먼저 생성할 것)
    try:
        from supabase_app.services import init_tables

        init_tables()
    except RuntimeError as e:
        # 테이블 없으면 앱 시작 시 경고만 출력하고 계속 진행
        print(f"[경고] Supabase 테이블 초기화 실패: {e}", file=sys.stderr)
        print(
            "Supabase SQL 에디터에서 테이블 생성 스크립트를 실행하세요.",
            file=sys.stderr,
        )

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", 5002))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
