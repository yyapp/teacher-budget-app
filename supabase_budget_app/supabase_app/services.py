"""비즈니스 로직. DB 작업을 캡슐화한다."""

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .db import client

# ---- 테이블 컬럼 정의 ----
# Supabase 테이블 컬럼과 매핑을 명확히 하기 위한 상수.
BUDGETS_TABLE = "budgets"
TRANSACTIONS_TABLE = "transactions"
CART_PROPOSALS_TABLE = "cart_proposals"
TRANSACTION_META_PREFIX = "__BUDGET_APP_META_V1__:"

# 거래 상태
STATUS_DRAFT = "draft"  # 임시
STATUS_CONFIRMED = "confirmed"  # 확정
STATUS_CANCELLED = "cancelled"  # 취소
STATUS_REFUNDED = "refunded"  # 환불됨 (거래 자체에 표시)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_decimal(value: Any) -> Decimal:
    """float/문자열을 Decimal로 변환."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(str(value))


def init_tables() -> None:
    """필요한 테이블과 인덱스가 존재하는지 확인한다.
    Supabase 콘솔에서 직접 테이블 생성하는 것을 권장하며,
    여기서는 최소한의 존재 확인만 한다."""
    svc = client()
    # 테이블 존재 확인용 간단한 쿼리. 없으면 오류 발생 → 콘솔에서 생성 필요.
    try:
        svc.table(BUDGETS_TABLE).select("id", head=True).execute()
        svc.table(TRANSACTIONS_TABLE).select("id", head=True).execute()
        svc.table(CART_PROPOSALS_TABLE).select("id", head=True).execute()
    except Exception:
        # 테이블이 없으면 여기서 오류 발생. 콘솔에서 먼저 생성할 것.
        raise RuntimeError(
            "Supabase 테이블(budgets, transactions, cart_proposals)이 없습니다. "
            "Supabase SQL 에디터에서 아래 스크립트를 실행하세요.\n"
            "참고: supabase_app/doc/table-init.sql"
        )


def _unpack_transaction(row: Dict[str, Any]) -> Dict[str, Any]:
    """Read app metadata from the existing notes column on older Supabase schemas."""
    transaction = dict(row)
    notes = transaction.get("notes") or ""
    if isinstance(notes, str) and notes.startswith(TRANSACTION_META_PREFIX):
        try:
            metadata = json.loads(notes[len(TRANSACTION_META_PREFIX) :])
        except (TypeError, ValueError):
            return transaction
        transaction["notes"] = metadata.get("notes", "")
        transaction["line_items"] = metadata.get("line_items", [])
        transaction["additional_document_path"] = metadata.get(
            "additional_document_path"
        )
        transaction["additional_document_filename"] = metadata.get(
            "additional_document_filename"
        )
        transaction["cart_summary"] = metadata.get("cart_summary")
    return transaction


# ===================== 예산 (Budgets) =====================


def list_budgets() -> List[Dict]:
    """예산 목록을 최신순으로 반환."""
    svc = client()
    result = (
        svc.table(BUDGETS_TABLE).select("*").order("updated_at", desc=True).execute()
    )
    return result.data if result.data else []


def get_budget(budget_id: str) -> Optional[Dict]:
    svc = client()
    result = svc.table(BUDGETS_TABLE).select("*").eq("id", budget_id).single().execute()
    return result.data if result.data else None


def create_budget(name: str, amount: float, category: str = "") -> Optional[Dict]:
    svc = client()
    now = _now_iso()
    row = {
        "id": str(uuid.uuid4()),
        "name": name.strip(),
        "amount": str(_to_decimal(amount)),
        "category": category.strip(),
        "created_at": now,
        "updated_at": now,
    }
    result = svc.table(BUDGETS_TABLE).insert(row).execute()
    return result.data[0] if result.data else None


def update_budget(
    budget_id: str,
    name: Optional[str],
    amount: Optional[float],
    category: Optional[str],
) -> bool:
    svc = client()
    updates: Dict[str, Any] = {"updated_at": _now_iso()}
    if name is not None:
        updates["name"] = name.strip()
    if amount is not None:
        updates["amount"] = str(_to_decimal(amount))
    if category is not None:
        updates["category"] = category.strip()
    result = svc.table(BUDGETS_TABLE).update(updates).eq("id", budget_id).execute()
    return bool(result.data)


def delete_budget(budget_id: str) -> bool:
    svc = client()
    result = svc.table(BUDGETS_TABLE).delete().eq("id", budget_id).execute()
    return bool(result.data)


def get_budget_balance(budget_id: str) -> Optional[float]:
    """예산 잔액 = 예산 금액 - 확정(+) 거래 합계 + 취소/환불 합계."""
    budget = get_budget(budget_id)
    if not budget:
        return None
    total_spent = sum_transactions_by_budget(budget_id, STATUS_CONFIRMED)
    total_cancelled = sum_transactions_by_budget(budget_id, STATUS_CANCELLED)
    total_refunded = sum_transactions_by_budget(budget_id, STATUS_REFUNDED)
    remaining = float(budget["amount"]) - total_spent + total_cancelled + total_refunded
    return remaining


# ===================== 거래 (Transactions) =====================


def list_transactions(
    budget_id: Optional[str] = None, status: Optional[str] = None, limit: int = 200
) -> List[Dict]:
    """거래 목록을 반환. budget_id나 status로 필터링 가능."""
    svc = client()
    query = svc.table(TRANSACTIONS_TABLE).select("*")
    if budget_id:
        query = query.eq("budget_id", budget_id)
    if status:
        query = query.eq("status", status)
    query = query.order("created_at", desc=True).limit(limit)
    result = query.execute()
    return [_unpack_transaction(row) for row in (result.data or [])]


def get_transaction(tx_id: str) -> Optional[Dict]:
    svc = client()
    result = (
        svc.table(TRANSACTIONS_TABLE).select("*").eq("id", tx_id).single().execute()
    )
    return _unpack_transaction(result.data) if result.data else None


def unique_evidence_filename(
    filename: str, reserved: Optional[List[str]] = None
) -> str:
    """Add a sequence suffix when this evidence filename is already used."""
    stem, extension = filename.rsplit(".", 1)
    used = set(reserved or [])
    svc = client()
    sequence = 1
    while True:
        candidate = filename if sequence == 1 else f"{stem}_{sequence}.{extension}"
        if candidate in used:
            sequence += 1
            continue
        try:
            result = (
                svc.table(TRANSACTIONS_TABLE)
                .select("id")
                .or_(
                    f"receipt_filename.eq.{candidate},"
                    f"additional_document_filename.eq.{candidate}"
                )
                .limit(1)
                .execute()
            )
        except Exception:
            # Older databases may not have the optional document columns yet.
            try:
                result = (
                    svc.table(TRANSACTIONS_TABLE)
                    .select("id")
                    .eq("receipt_filename", candidate)
                    .limit(1)
                    .execute()
                )
            except Exception:
                return candidate
        if not result.data:
            return candidate
        sequence += 1


def create_transaction(
    budget_id: str,
    description: str,
    amount: float,
    category: str = "",
    quantity: int = 1,
    unit_price: Optional[float] = None,
    receipt_path: Optional[str] = None,
    receipt_filename: Optional[str] = None,
    additional_document_path: Optional[str] = None,
    additional_document_filename: Optional[str] = None,
    line_items: Optional[List[Dict[str, Any]]] = None,
    cart_summary: Optional[Dict[str, Any]] = None,
    status: str = STATUS_DRAFT,
    notes: str = "",
) -> Optional[Dict]:
    """거래를 생성(임시 상태)."""
    svc = client()
    now = _now_iso()
    metadata = {
        "notes": notes.strip(),
        "line_items": line_items or [],
        "additional_document_path": additional_document_path,
        "additional_document_filename": additional_document_filename,
        "cart_summary": cart_summary,
    }
    stored_notes = notes.strip()
    if (
        line_items
        or additional_document_path
        or additional_document_filename
        or cart_summary
    ):
        stored_notes = TRANSACTION_META_PREFIX + json.dumps(
            metadata, ensure_ascii=False, separators=(",", ":")
        )
    row = {
        "id": str(uuid.uuid4()),
        "budget_id": budget_id,
        "description": description.strip(),
        "amount": str(_to_decimal(amount)),
        "category": category.strip(),
        "quantity": quantity,
        "unit_price": str(_to_decimal(unit_price)) if unit_price is not None else None,
        "receipt_path": receipt_path,
        "receipt_filename": receipt_filename,
        "notes": stored_notes,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    result = svc.table(TRANSACTIONS_TABLE).insert(row).execute()
    return _unpack_transaction(result.data[0]) if result.data else None


def update_transaction(
    tx_id: str,
    description: Optional[str] = None,
    amount: Optional[float] = None,
    category: Optional[str] = None,
    quantity: Optional[int] = None,
    unit_price: Optional[float] = None,
    receipt_path: Optional[str] = None,
    receipt_filename: Optional[str] = None,
    notes: Optional[str] = None,
    status: Optional[str] = None,
) -> bool:
    """거래 정보를 수정한다. 이미 확정된 거래도 수정 가능(단, 비즈니즈 룰은 호출 측에서 확인).
    status를 주면 상태도 함께 변경한다."""
    svc = client()
    updates: Dict[str, Any] = {"updated_at": _now_iso()}
    if description is not None:
        updates["description"] = description.strip()
    if amount is not None:
        updates["amount"] = str(_to_decimal(amount))
    if category is not None:
        updates["category"] = category.strip()
    if quantity is not None:
        updates["quantity"] = quantity
    if unit_price is not None:
        updates["unit_price"] = str(_to_decimal(unit_price))
    if receipt_path is not None:
        updates["receipt_path"] = receipt_path
    if receipt_filename is not None:
        updates["receipt_filename"] = receipt_filename
    if notes is not None:
        updates["notes"] = notes.strip()
    if status is not None:
        updates["status"] = status
    result = svc.table(TRANSACTIONS_TABLE).update(updates).eq("id", tx_id).execute()
    return bool(result.data)


def confirm_transaction(tx_id: str) -> bool:
    """거래를 확정 상태로 변경."""
    tx = get_transaction(tx_id)
    if not tx:
        return False
    if tx.get("status") != STATUS_DRAFT:
        return False
    return update_transaction(tx_id, status=STATUS_CONFIRMED)


def cancel_transaction(tx_id: str) -> bool:
    """거래를 취소 상태로 변경."""
    tx = get_transaction(tx_id)
    if not tx:
        return False
    if tx.get("status") != STATUS_DRAFT:
        return False
    return update_transaction(tx_id, status=STATUS_CANCELLED)


def refund_transaction(tx_id: str, refund_amount: float, reason: str = "") -> bool:
    """확정된 거래를 환불 처리한다.
    거래 상태에 'refunded'를 표시하고, 별도 환불 기록(거래)은 남기지 않고
    거래 자체에 refund_amount와 refund_reason을 저장한다."""
    tx = get_transaction(tx_id)
    if not tx:
        return False
    if tx.get("status") != STATUS_CONFIRMED:
        return False
    updates: Dict[str, Any] = {
        "status": STATUS_REFUNDED,
        "refund_amount": str(_to_decimal(refund_amount)),
        "refund_reason": reason.strip(),
        "updated_at": _now_iso(),
    }
    result = svc.table(TRANSACTIONS_TABLE).update(updates).eq("id", tx_id).execute()
    return bool(result.data)


def delete_transaction(tx_id: str) -> bool:
    """거래를 삭제한다. 확정·환불된 거래는 삭제 제한."""
    tx = get_transaction(tx_id)
    if not tx:
        return False
    if tx.get("status") in (STATUS_CONFIRMED, STATUS_REFUNDED, STATUS_CANCELLED):
        return False  # 확정된 거래는 삭제 불가
    svc = client()
    result = svc.table(TRANSACTIONS_TABLE).delete().eq("id", tx_id).execute()
    return bool(result.data)


def sum_transactions_by_budget(budget_id: str, status: str) -> float:
    """특정 예산과 상태에서 거래 금액 합계를 반환."""
    svc = client()
    result = (
        svc.table(TRANSACTIONS_TABLE)
        .select("amount", count="exact")
        .eq("budget_id", budget_id)
        .eq("status", status)
        .execute()
    )
    # Supabase Python 클라이언트에서 sum을 직접 지원하지 않으므로 개별 합산
    rows = result.data if result.data else []
    return sum(float(row.get("amount", 0) or 0) for row in rows)


# ===================== 장바구니/품의 (Cart Proposals) =====================


def create_cart_proposal(items: List[Dict], notes: str = "") -> Optional[Dict]:
    """장바구니 품목을 저장한 품의 초안을 생성."""
    svc = client()
    now = _now_iso()
    row = {
        "id": str(uuid.uuid4()),
        "items": items,
        "notes": notes.strip(),
        "created_at": now,
        "updated_at": now,
    }
    result = svc.table(CART_PROPOSALS_TABLE).insert(row).execute()
    return result.data[0] if result.data else None


def get_cart_proposal(proposal_id: str) -> Optional[Dict]:
    svc = client()
    result = (
        svc.table(CART_PROPOSALS_TABLE)
        .select("*")
        .eq("id", proposal_id)
        .single()
        .execute()
    )
    return result.data if result.data else None


def list_cart_proposals(limit: int = 50) -> List[Dict]:
    svc = client()
    result = (
        svc.table(CART_PROPOSALS_TABLE)
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data if result.data else []
