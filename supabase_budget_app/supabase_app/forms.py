"""입력 검증 헬퍼. 숫자, 파일, 일반 텍스트 검증."""

from typing import Any, Optional


def safe_float(value: Any, default: float = 0.0) -> float:
    """문자열을 float로 안전하게 변환한다. 실패 시 default를 반환한다."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return default
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return default
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """문자열을 int로 안전하게 변환한다."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return default
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return default
    try:
        return int(cleaned)
    except (ValueError, TypeError):
        return default


def non_empty_str(value: Any, default: str = "") -> str:
    """비어있지 않은 문자열을 반환한다. 비어있으면 default."""
    if value is None:
        return default
    s = str(value).strip()
    return s if s else default


def is_valid_amount(value: Any) -> bool:
    """금액이 유효하는지 (0 이상, 숫자)."""
    f = safe_float(value)
    return f >= 0


def is_valid_quantity(value: Any) -> bool:
    """수량이 유효하는지 (1 이상 정수)."""
    i = safe_int(value)
    return i >= 1
