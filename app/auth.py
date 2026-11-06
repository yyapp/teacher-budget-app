# -*- coding: utf-8 -*-
"""
teacher-budget-app / app/auth.py

선택형 접속 비밀번호. 환경변수 APP_ACCESS_PASSWORD가 설정된 경우에만 로그인을 요구한다.
(설정이 없으면 예전처럼 로그인 없이 동작한다. 비밀번호를 코드나 저장소에 적지 말 것.)

- 로그인 시도 제한: 같은 접속자는 5번 틀리면 10분간 잠김, 전체로는 10분에 50번까지.
- 비밀번호 비교는 시간 차이로 추측하기 어렵게 hmac.compare_digest를 쓴다.
"""

import hmac
import os
import time
from collections import defaultdict, deque

from flask import Blueprint, redirect, render_template_string, request, session, url_for

auth_bp = Blueprint("auth", __name__)

WINDOW_SECONDS = 10 * 60
MAX_FAILS_PER_CLIENT = 5
MAX_FAILS_TOTAL = 50

_fails_by_client = defaultdict(deque)
_fails_total = deque()


def _password() -> str:
    return os.environ.get("APP_ACCESS_PASSWORD", "")


def auth_enabled() -> bool:
    return bool(_password())


def _client_id() -> str:
    # Cloudflare 터널을 거치면 실제 접속자 주소가 이 헤더에 들어온다.
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "unknown"


def _prune(queue: deque, now: float) -> None:
    while queue and now - queue[0] > WINDOW_SECONDS:
        queue.popleft()


def _is_locked(client: str, now: float) -> bool:
    mine = _fails_by_client[client]
    _prune(mine, now)
    _prune(_fails_total, now)
    return len(mine) >= MAX_FAILS_PER_CLIENT or len(_fails_total) >= MAX_FAILS_TOTAL


def _record_fail(client: str, now: float) -> None:
    _fails_by_client[client].append(now)
    _fails_total.append(now)


def reset_for_tests() -> None:
    _fails_by_client.clear()
    _fails_total.clear()


LOGIN_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>접속 비밀번호</title>
<style>
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Segoe UI",sans-serif; background:#f5f7fb; color:#1f2937; display:flex; min-height:100vh; align-items:center; justify-content:center; }
  .box { background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:28px; width:min(92vw,380px); }
  h1 { font-size:19px; margin:0 0 6px; } p { font-size:13px; color:#64748b; margin:0 0 16px; }
  input { width:100%; box-sizing:border-box; padding:11px 12px; border:1px solid #cbd5e1; border-radius:8px; font-size:16px; margin-bottom:12px; }
  button { width:100%; padding:11px; border:0; border-radius:8px; background:#2563eb; color:#fff; font-size:15px; cursor:pointer; }
  .err { background:#fef2f2; border:1px solid #fecaca; color:#dc2626; padding:9px 11px; border-radius:8px; font-size:13px; margin-bottom:12px; }
</style></head>
<body><form class="box" method="post" action="{{ url_for('auth.login') }}">
  <h1>교사용 예산·구매 서류 도우미</h1>
  <p>시연용 페이지입니다. 접속 비밀번호를 입력해 주세요.</p>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <input type="password" name="password" placeholder="접속 비밀번호" autocomplete="current-password" autofocus required>
  <input type="hidden" name="next" value="{{ next }}">
  <button type="submit">들어가기</button>
</form></body></html>"""


def _safe_next(target: str) -> str:
    """로그인 뒤 돌아갈 주소는 이 사이트 안의 경로만 허용한다."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("routes.index")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled():
        return redirect(url_for("routes.index"))

    next_url = _safe_next(request.values.get("next", ""))
    if request.method == "POST":
        client, now = _client_id(), time.time()
        if _is_locked(client, now):
            return render_template_string(LOGIN_HTML, error="시도 횟수를 넘어 잠시 잠겼습니다. 10분 뒤에 다시 시도해 주세요.", next=next_url), 429
        given = (request.form.get("password") or "").encode("utf-8")
        if hmac.compare_digest(given, _password().encode("utf-8")):
            session.clear()
            session["authed"] = True
            session.permanent = False
            return redirect(next_url)
        _record_fail(client, now)
        return render_template_string(LOGIN_HTML, error="비밀번호가 맞지 않습니다.", next=next_url), 401
    return render_template_string(LOGIN_HTML, error=None, next=next_url)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login") if auth_enabled() else url_for("routes.index"))


def init_auth(app) -> None:
    """앱에 로그인 검사를 붙인다. 비밀번호가 설정돼 있을 때만 동작한다."""
    app.register_blueprint(auth_bp)
    if os.environ.get("APP_COOKIE_SECURE") == "1":
        app.config["SESSION_COOKIE_SECURE"] = True  # https(터널)로만 쿠키 전송
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    @app.before_request
    def require_login():
        if not auth_enabled():
            return None
        if request.endpoint in ("auth.login", "auth.logout") or session.get("authed"):
            return None
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    @app.after_request
    def no_store(resp):
        if auth_enabled():
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        return resp

    @app.context_processor
    def inject_auth():
        return {"auth_enabled": auth_enabled()}
