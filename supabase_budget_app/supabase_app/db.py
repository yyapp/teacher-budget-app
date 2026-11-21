"""Supabase 클라이언트 초기화 및 헬퍼."""

from os import environ
from typing import Any, Dict, List, Optional

from supabase import Client, create_client


def get_supabase() -> Client:
    """Supabase 클라이언트를 반환한다. 앱 시작 시 한 번만 호출되도록 한다."""
    url = environ.get("SUPABASE_URL")
    key = environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY 환경변수가 필요합니다. "
            ".env 파일을 확인하세요."
        )
    return create_client(url, key)


# 모듈 레벨 캐시. 앱당 하나의 클라이언트만 사용한다.
_client: Optional[Client] = None


def client() -> Client:
    """캐시된 Supabase 클라이언트를 반환한다."""
    global _client
    if _client is None:
        _client = get_supabase()
    return _client


def reset_client() -> None:
    """테스트 등에서 클라이언트를 초기화한다."""
    global _client
    _client = None
