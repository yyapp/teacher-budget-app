"""Supabase Storage 헬퍼. 영수증 파일 업로드/다운로드를 담당한다."""

import logging
import uuid
from typing import Optional

from .db import client

# Storage 버킷 이름. Supabase 콘솔에서 미리 만들어두어야 한다.
BUCKET_NAME = "receipts"
logger = logging.getLogger(__name__)


def _ensure_bucket() -> None:
    """버킷이 존재하는지 확인하고 없으면 생성을 시도한다.
    운영 시에는 Supabase 콘솔에서 직접 생성하는 것을 권장한다."""
    svc = client()
    try:
        svc.storage.get_bucket(BUCKET_NAME)
    except Exception:
        try:
            svc.storage.create_bucket(BUCKET_NAME, options={"public": False})
        except Exception:
            # Another request may have created it after get_bucket failed.
            svc.storage.get_bucket(BUCKET_NAME)


def upload_receipt(
    file_bytes: bytes, original_filename: str, content_type: str
) -> Optional[str]:
    """영수증 파일을 올리고 만료되지 않는 Storage object path를 반환한다."""
    try:
        _ensure_bucket()
        svc = client()
        file_id = uuid.uuid4().hex[:12]
        extension = original_filename.rsplit(".", 1)[-1].lower()
        path = f"raw/{file_id}/evidence.{extension}"
        svc.storage.from_(BUCKET_NAME).upload(
            path,
            file_bytes,
            file_options={"content-type": content_type, "upsert": "false"},
        )
        return path
    except Exception:
        logger.exception("Supabase receipt upload failed")
        return None


def signed_receipt_url(
    file_path: Optional[str], expires_in: int = 3600
) -> Optional[str]:
    """비공개 영수증 파일의 임시 열람 링크를 만든다."""
    if not file_path:
        return None
    if file_path.startswith("http"):
        return file_path
    svc = client()
    try:
        result = svc.storage.from_(BUCKET_NAME).create_signed_url(file_path, expires_in)
        return result.get("signedUrl") or result.get("signedURL")
    except Exception:
        return None


def delete_receipt(file_path: str) -> bool:
    """Storage에서 파일을 삭제한다."""
    _ensure_bucket()
    svc = client()
    try:
        svc.storage.from_(BUCKET_NAME).remove([file_path])
        return True
    except Exception:
        return False


def list_receipts() -> list:
    """Storage 버킷의 파일 목록을 반환한다."""
    _ensure_bucket()
    svc = client()
    try:
        data = svc.storage.from_(BUCKET_NAME).list()
        return data if isinstance(data, list) else []
    except Exception:
        return []
