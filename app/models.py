# -*- coding: utf-8 -*-
"""
teacher-budget-app / app/models.py

입력 레코드 검증 헬퍼.
빈 값과 0을 구분하고, 금액/수량/단위 같은 기본 입력 규칙을 정리한다.
"""

from typing import Any, Dict, Optional


def is_empty_number(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return True
        try:
            float(stripped)
            return False
        except ValueError:
            return True
    try:
        if value != value:  # NaN
            return True
    except Exception:
        pass
    return False


def to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if default is None:
        default = 0.0
    if value is None:
        return default
    if isinstance(value, (int, float)):
        if value != value:
            return default
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return default
        try:
            return float(stripped)
        except ValueError:
            return default
    return default


def to_int(value: Any, default: int = 0) -> int:
    f = to_float(value, float(default))
    if f is None:
        return default
    return int(f)


def validate_item_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "item_name": str(row.get("item_name") or "").strip(),
        "specification": str(row.get("specification") or "").strip(),
        "quantity": to_float(row.get("quantity"), 0.0),
        "unit": str(row.get("unit") or "").strip(),
        "unit_price": to_float(row.get("unit_price"), 0.0),
        "item_amount": to_float(row.get("item_amount"), 0.0),
        "selection_status": str(row.get("selection_status") or "선택").strip(),
    }


def validate_transaction_input(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "budget_id": str(data.get("budget_id") or "").strip(),
        "merchant_name": str(data.get("merchant_name") or "").strip(),
        "transaction_date": str(data.get("transaction_date") or "").strip(),
        "total_amount": to_float(data.get("total_amount"), 0.0),
        "status": str(data.get("status") or "임시").strip(),
    }
