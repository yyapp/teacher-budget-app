# -*- coding: utf-8 -*-
"""
teacher-budget-app / app.py

로컬 웹앱 진입점.
"""

import os
from pathlib import Path
from flask import Flask, flash, redirect, url_for

from app.forms import MAX_UPLOAD_BYTES
from app.routes import routes_bp

def _load_dotenv(path: Path) -> None:
    """프로젝트 폴더의 .env(KEY=VALUE)를 읽어 환경변수에 넣는다. 이미 있는 값은 덮어쓰지 않는다."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv(Path(__file__).resolve().parent / ".env")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "local-dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 2 * 1024 * 1024  # 폼 여유분 포함
app.register_blueprint(routes_bp)


@app.errorhandler(413)
def too_large(_error):
    flash("파일이 너무 큽니다. 10MB 이하만 올릴 수 있습니다.", "error")
    return redirect(url_for("routes.upload"))

if __name__ == "__main__":
    from app.database import init_db
    init_db()
    port = int(os.environ.get("FLASK_PORT", "5002"))
    app.run(debug=False, host="127.0.0.1", port=port)
