# -*- coding: utf-8 -*-
"""
teacher-budget-app / app/storage.py

원본/정리본/JSON 증빙 파일과 파싱 결과(초안)의 로컬 저장 규칙을 다룬다.
파일명과 경로는 여기서 관리한다.
"""

import json
import os
import uuid
import re
import json

from datetime import datetime, timezone

STORAGE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage")
RECEIPTS_DIR = os.path.join(STORAGE_ROOT, "receipts")
PARSED_DIR = os.path.join(STORAGE_ROOT, "parsed")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs():
    os.makedirs(RECEIPTS_DIR, exist_ok=True)
    os.makedirs(PARSED_DIR, exist_ok=True)


def safe_filename(name: str) -> str:
    # 금지 문자 제거 및 공백 정리
    cleaned = re.sub(r"[\\/:*?\"<>|]", "", name)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    if not cleaned:
        cleaned = "file"
    return cleaned


def suggest_receipt_original_name(budget_name: str, merchant: str, serial: str, original_ext: str) -> str:
    """선택한 예산명_거래처_일련번호 원본 파일명"""
    base = f"{safe_filename(budget_name)}_{safe_filename(merchant)}_{safe_filename(serial)}"
    ext = "." + safe_filename(original_ext.lstrip("."))
    return f"{base}_원본{ext}"


def suggest_receipt_clean_name(budget_name: str, merchant: str, serial: str, original_ext: str) -> str:
    """선택한 예산명_거래처_일련번호 정리본 파일명"""
    base = f"{safe_filename(budget_name)}_{safe_filename(merchant)}_{safe_filename(serial)}"
    ext = "." + safe_filename(original_ext.lstrip("."))
    return f"{base}_정리본{ext}"


def suggest_json_file_name(date: str, budget_name: str, index: int) -> str:
    base = f"{date}_{budget_name}_{index}"
    return f"{safe_filename(base)}.json"


def suggest_original_file_name(date: str, budget_name: str, index: int, original_ext: str) -> str:
    base = f"{date}_{budget_name}_{index}"
    ext = "." + safe_filename(original_ext.lstrip("."))
    return f"{safe_filename(base)}_원본{ext}"


def suggest_clean_file_name(date: str, budget_name: str, index: int, original_ext: str) -> str:
    base = f"{date}_{budget_name}_{index}"
    ext = "." + safe_filename(original_ext.lstrip("."))
    return f"{safe_filename(base)}_정리본{ext}"


MAX_NAME_PART = 40  # 파일명에 넣는 예산명·거래처 길이 제한


def _name_part(value: str, fallback: str) -> str:
    cleaned = safe_filename(value or "")
    if cleaned == "file" and not (value or "").strip():
        cleaned = fallback
    return cleaned[:MAX_NAME_PART].strip("._") or fallback


def evidence_file_name(budget_name: str, date: str, merchant: str, ext: str, number: int | None = None) -> str:
    """증빙 파일명: 예산명_일자_거래처.확장자 (같은 거래에 파일이 여럿이면 _2, _3 …)"""
    date_part = (date or "").strip()
    if not date_part or date_part == "미확인":
        date_part = "일자미확인"
    parts = [_name_part(budget_name, "예산미확인"), _name_part(date_part, "일자미확인"), _name_part(merchant, "거래처미확인")]
    suffix = f"_{number}" if number and number > 1 else ""
    return "_".join(parts) + suffix + "." + safe_filename(ext.lstrip(".") or "bin")


def unique_name(directory: str, name: str, current_path: str | None = None) -> str:
    """directory 안에서 겹치지 않는 파일명. current_path가 이미 그 이름의 파일이면 그대로 쓴다."""
    stem, ext = os.path.splitext(name)
    candidate, n = name, 1
    while True:
        path = os.path.join(directory, candidate)
        if not os.path.exists(path):
            return candidate
        if current_path and os.path.exists(current_path) and os.path.samefile(path, current_path):
            return candidate
        n += 1
        candidate = f"{stem}_{n}{ext}"


def save_file(content: bytes, suggested_name: str) -> str:
    ensure_dirs()
    path = os.path.join(RECEIPTS_DIR, safe_filename(suggested_name))
    with open(path, "wb") as f:
        f.write(content)
    return path


def build_file_record(
    file_type: str,
    transaction_id: str,
    date: str,
    budget_name: str,
    index: int,
    original_ext: str,
    original_total: float | None,
    content: bytes | None = None,
) -> dict:
    index_hex = uuid.uuid4().hex[:6]
    unique_index = f"{index}_{index_hex}"

    if file_type == "원본":
        file_name = suggest_original_file_name(date, budget_name, unique_index, original_ext)
    elif file_type == "정리본":
        file_name = suggest_clean_file_name(date, budget_name, unique_index, original_ext)
    else:
        file_name = suggest_json_file_name(date, budget_name, unique_index)

    file_path = ""
    if content is not None:
        file_path = save_file(content, file_name)

    return {
        "file_name": file_name,
        "file_path": file_path,
        "original_total": original_total,
    }


def parsed_cache_path(budget_name: str, index: str) -> str:
    """파싱 결과 캐시 JSON의 저장 경로"""
    ensure_dirs()
    safe_budget = safe_filename(budget_name)
    safe_index = safe_filename(index)
    name = f"{safe_budget}_{safe_index}_parsed.json"
    return os.path.join(PARSED_DIR, name)


def save_parsed_cache(budget_name: str, index: str, payload: dict) -> str:
    """파싱 결과 초안을 로컬 캐시 JSON으로 저장"""
    path = parsed_cache_path(budget_name, index)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_parsed_cache(budget_name: str, index: str) -> dict | None:
    """로컬에 저장된 파싱 결과 초안을 불러온다. 없으면 None"""
    path = parsed_cache_path(budget_name, index)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
