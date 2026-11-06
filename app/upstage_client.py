# -*- coding: utf-8 -*-
"""
teacher-budget-app / app/upstage_client.py

Upstage Information Extraction API로 영수증 이미지에서 거래처·날짜·총액·품목을 뽑는다.
  POST https://api.upstage.ai/v1/information-extraction/chat/completions
API 키는 호출하는 쪽이 환경변수에서 읽어 넘긴다. 로그·에러·반환값에는 키를 절대 넣지 않는다.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict, List, Optional

import requests

UPSTAGE_EXTRACT_URL = "https://api.upstage.ai/v1/information-extraction/chat/completions"
UPSTAGE_EXTRACT_MODEL = "information-extract"

RECEIPT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "merchant_name": {"type": "string", "description": "상호명 또는 판매처 이름"},
        "transaction_date": {"type": "string", "description": "결제·구매 날짜. YYYY-MM-DD 형식"},
        "total_amount": {"type": "number", "description": "최종 결제 금액(원). 숫자만"},
        "items": {
            "type": "array",
            "description": "구매한 품목 목록",
            "items": {
                "type": "object",
                "properties": {
                    "item_name": {"type": "string", "description": "품목명"},
                    "specification": {"type": "string", "description": "규격 또는 옵션"},
                    "quantity": {"type": "number", "description": "수량"},
                    "unit": {"type": "string", "description": "단위(개, 권 등)"},
                    "unit_price": {"type": "number", "description": "단가(원)"},
                    "item_amount": {"type": "number", "description": "품목 금액(원)"},
                },
            },
        },
    },
}


def _fail(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": message}


def _to_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.\-]", "", value)
        try:
            return float(cleaned) if cleaned not in ("", "-", ".") else None
        except ValueError:
            return None
    return None


def _to_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_date(value: Any) -> str:
    """'2026.11.05', '2026/11/5' 같은 값을 YYYY-MM-DD로 맞춘다. 못 맞추면 빈 문자열."""
    m = re.search(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})", _to_text(value))
    if not m:
        return ""
    year, month, day = (int(g) for g in m.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def interpret_extraction(data: Any) -> Dict[str, Any]:
    """추출된 JSON(dict)을 앱에서 쓰는 형태로 정리한다."""
    if not isinstance(data, dict):
        return _fail("인식 결과가 예상한 형식이 아닙니다.")

    items: List[Dict[str, Any]] = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        name = _to_text(raw.get("item_name"))
        if not name:
            continue
        quantity = _to_number(raw.get("quantity"))
        unit_price = _to_number(raw.get("unit_price"))
        amount = _to_number(raw.get("item_amount"))
        if quantity is None or quantity <= 0:
            quantity = 1.0
        if amount is None and unit_price is not None:
            amount = unit_price * quantity
        if unit_price is None and amount is not None:
            unit_price = amount / quantity
        items.append({
            "item_name": name,
            "specification": _to_text(raw.get("specification")),
            "quantity": quantity,
            "unit": _to_text(raw.get("unit")) or "개",
            "unit_price": max(unit_price or 0.0, 0.0),
            "item_amount": max(amount or 0.0, 0.0),
            "selection_status": "선택",
        })

    total = _to_number(data.get("total_amount"))
    if total is not None and total < 0:
        total = None

    return {
        "ok": True,
        "merchant": _to_text(data.get("merchant_name")),
        "date": _normalize_date(data.get("transaction_date")),
        "total_amount": total,
        "items": items,
        "raw_text": json.dumps(data, ensure_ascii=False),
        "note": "자동 인식 결과입니다. 거래처·금액·품목을 꼭 확인하고 고쳐 주세요.",
    }


def parse_receipt_image(image_bytes: bytes, api_key: str, *, timeout_seconds: int = 60) -> Dict[str, Any]:
    """영수증 이미지를 Upstage로 보내 구조화된 결과를 돌려준다. 실패해도 예외 대신 {ok: False, error}."""
    if not api_key or not isinstance(api_key, str):
        return _fail("API 키가 설정되지 않았습니다.")
    if not image_bytes:
        return _fail("이미지가 비어 있습니다.")

    encoded = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": UPSTAGE_EXTRACT_MODEL,
        "messages": [{
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": f"data:application/octet-stream;base64,{encoded}"}}],
        }],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "receipt_schema", "schema": RECEIPT_SCHEMA},
        },
    }

    try:
        resp = requests.post(
            UPSTAGE_EXTRACT_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=timeout_seconds,
        )
    except requests.exceptions.Timeout:
        return _fail("인식 서버 응답이 늦어 중단했습니다. 잠시 뒤 다시 시도해 주세요.")
    except requests.exceptions.RequestException:
        return _fail("인식 서버에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.")

    if resp.status_code in (401, 403):
        return _fail("API 키가 올바르지 않거나 권한이 없습니다.")
    if resp.status_code == 429:
        return _fail("인식 요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요.")
    if resp.status_code >= 400:
        return _fail(f"인식 서버 오류가 났습니다. (코드 {resp.status_code})")

    try:
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(content) if isinstance(content, str) else content
    except (ValueError, KeyError, IndexError, TypeError):
        return _fail("인식 결과를 해석하지 못했습니다.")

    return interpret_extraction(data)
