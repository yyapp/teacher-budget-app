# -*- coding: utf-8 -*-
"""
teacher-budget-app / app/forms.py

웹 폼 입력 검증. 잘못된 값은 InputError(사용자용 한국어 메시지)로 알린다.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_UPLOAD_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".pdf")


class InputError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 입력 오류."""


def parse_amount(text: Optional[str], label: str, default: float = 0.0) -> float:
    """숫자 문자열을 float로 바꾼다. 빈 값은 default, 숫자가 아니거나 음수면 InputError."""
    cleaned = (text or "").strip().replace(",", "")
    if not cleaned:
        return default
    try:
        value = float(cleaned)
    except ValueError:
        raise InputError(f"{label}을(를) 숫자로 입력해 주세요.")
    if value != value or value in (float("inf"), float("-inf")):
        raise InputError(f"{label}을(를) 올바른 숫자로 입력해 주세요.")
    if value < 0:
        raise InputError(f"{label}은(는) 0 이상이어야 합니다.")
    return value


def _at(values: List[str], index: int) -> str:
    return values[index] if index < len(values) else ""


def parse_items(form) -> List[Dict[str, Any]]:
    """품목 입력 줄(item_name 등)을 읽어 품목 목록으로 만든다."""
    names = form.getlist("item_name")
    specs = form.getlist("item_specification")
    qtys = form.getlist("item_quantity")
    units = form.getlist("item_unit")
    unit_prices = form.getlist("item_unit_price")
    amounts = form.getlist("item_amount")

    items = []
    for i, name in enumerate(names):
        name = (name or "").strip()
        if not name:
            continue
        label = f"'{name}'"
        items.append({
            "item_name": name,
            "specification": _at(specs, i).strip(),
            "quantity": parse_amount(_at(qtys, i), f"{label}의 수량"),
            "unit": _at(units, i).strip() or "개",
            "unit_price": parse_amount(_at(unit_prices, i), f"{label}의 단가"),
            "item_amount": parse_amount(_at(amounts, i), f"{label}의 금액"),
            "selection_status": "선택",
        })
    return items


def _looks_like_image(head: bytes, ext: str) -> bool:
    """확장자와 파일 앞부분(시그니처)이 맞는지 확인한다. PDF도 같은 방식으로 검사."""
    if ext == ".pdf":
        return head.startswith(b"%PDF-")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return False


def read_upload(file_storage) -> Tuple[Optional[bytes], str]:
    """업로드 파일을 검사해 (내용, 확장자)를 돌려준다. 파일이 없으면 (None, '.png')."""
    if not file_storage or not file_storage.filename:
        return None, ".png"

    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise InputError("이미지(png, jpg, jpeg, webp) 또는 PDF 파일만 올릴 수 있습니다.")

    content = file_storage.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise InputError("빈 파일입니다. 다른 파일을 선택해 주세요.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise InputError("파일이 너무 큽니다. 10MB 이하만 올릴 수 있습니다.")
    if not _looks_like_image(content[:16], ext):
        raise InputError("이미지 또는 PDF 파일이 아니거나 손상된 파일입니다.")
    return content, ext
