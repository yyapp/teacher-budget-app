# -*- coding: utf-8 -*-
"""
teacher-budget-app / app/status.py

작업 상태와 요청 이력을 다룬다.
파일 업로드, 장부 기록, 저장 완료, 실패, 충돌 등을 구분한다.
"""

from datetime import datetime, timezone

from app.database import get_connection, _now_iso

STATUS_TEMPORARY = "임시"
STATUS_IN_PROGRESS = "진행중"
STATUS_SUCCESS = "성공"
STATUS_FAILED = "실패"
STATUS_PARTIAL = "부분성공"
STATUS_CONFLICT = "충돌"
STATUS_OFFLINE_DRAFT = "오프라인초안"


def log_request(kind: str, status: str, related_transaction_id: str | None = None, related_receipt_id: str | None = None, error_note: str | None = None):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO request_log
                (request_id, kind, status, related_transaction_id, related_receipt_id, error_note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid_hex(),
                kind,
                status,
                related_transaction_id,
                related_receipt_id,
                error_note,
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def uuid_hex():
    import uuid
    return uuid.uuid4().hex
