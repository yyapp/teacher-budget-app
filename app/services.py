# -*- coding: utf-8 -*-
"""
teacher-budget-app / app/services.py

초안 처리, 예산 선택, 저장 흐름, 잔액 계산을 다룬다.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.database import get_connection, _now_iso
from app.models import is_empty_number, to_float, to_int, validate_transaction_input, validate_item_row
from app.status import log_request, STATUS_TEMPORARY, STATUS_SUCCESS, STATUS_FAILED, STATUS_PARTIAL, uuid_hex
from app.storage import build_file_record


def create_budget(budget_name: str, year: int, type_: str, allocated_amount: float, applied_date: str = "") -> Dict[str, Any]:
    conn = get_connection()
    try:
        now = _now_iso()
        budget_id = uuid_hex()
        conn.execute(
            """
            INSERT INTO budget (budget_id, budget_name, year, type, allocated_amount, applied_date, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (budget_id, budget_name.strip(), year, type_, allocated_amount, applied_date.strip(), now, now),
        )
        conn.commit()
        return {"budget_id": budget_id, "budget_name": budget_name, "year": year, "type": type_, "allocated_amount": allocated_amount}
    finally:
        conn.close()


def list_budgets():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM budget ORDER BY year DESC, budget_name ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_budget(budget_id: str, budget_name: str, year: int, type_: str, allocated_amount: float, applied_date: str = "") -> Dict[str, Any]:
    conn = get_connection()
    try:
        now = _now_iso()
        conn.execute(
            """
            UPDATE budget
            SET budget_name = ?,
                year = ?,
                type = ?,
                allocated_amount = ?,
                applied_date = ?,
                updated_at = ?
            WHERE budget_id = ?
            """,
            (budget_name.strip(), year, type_, allocated_amount, applied_date.strip(), now, budget_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM budget WHERE budget_id = ?", (budget_id,)).fetchone()
        if not updated:
            return {"ok": False, "error": "예산을 찾을 수 없습니다."}
        result = {
            "ok": True,
            "budget_id": budget_id,
            "budget_name": updated["budget_name"],
            "year": updated["year"],
            "type": updated["type"],
            "allocated_amount": updated["allocated_amount"],
            "applied_date": updated["applied_date"],
        }
    finally:
        conn.close()

    # 예산명이 바뀌었을 수 있으니 이 예산 거래의 증빙 파일 이름도 맞춘다.
    sync_budget_receipt_names(budget_id)
    return result


def save_transaction_with_items(
    budget_id: str,
    merchant_name: str,
    transaction_date: str,
    total_amount: float,
    status: str,
    items: List[Dict[str, Any]],
    allocations: List[Dict[str, Any]],
    receipt_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    conn = get_connection()
    try:
        now = _now_iso()
        transaction_id = uuid_hex()

        conn.execute(
            """
            INSERT INTO transactions (transaction_id, budget_id, merchant_name, transaction_date, total_amount, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (transaction_id, budget_id, merchant_name.strip(), transaction_date.strip(), total_amount, status, now, now),
        )

        saved_items = []
        for item in items:
            row = validate_item_row(item)
            if not row["item_name"]:
                continue
            item_id = uuid_hex()
            conn.execute(
                """
                INSERT INTO item (item_id, transaction_id, item_name, specification, quantity, unit, unit_price, item_amount, selection_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    transaction_id,
                    row["item_name"],
                    row["specification"],
                    row["quantity"],
                    row["unit"],
                    row["unit_price"],
                    row["item_amount"],
                    row["selection_status"],
                    now,
                    now,
                ),
            )
            saved_items.append(item_id)

        saved_allocations = []
        for alloc in allocations:
            allocation_id = uuid_hex()
            conn.execute(
                """
                INSERT INTO budget_allocation (allocation_id, transaction_id, budget_id, allocated_amount, unallocated_amount, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    allocation_id,
                    transaction_id,
                    alloc["budget_id"],
                    to_float(alloc.get("allocated_amount"), 0.0),
                    to_float(alloc.get("unallocated_amount")),
                    now,
                ),
            )
            saved_allocations.append(allocation_id)

        receipt_id = None
        if receipt_meta:
            file_type = receipt_meta.get("file_type")
            if file_type in ("원본", "정리본", "json"):
                record = build_file_record(
                    file_type=file_type,
                    transaction_id=transaction_id,
                    date=receipt_meta.get("date", ""),
                    budget_name=receipt_meta.get("budget_name", ""),
                    index=to_int(receipt_meta.get("index", 0), 1),
                    original_ext=receipt_meta.get("original_ext", ".pdf"),
                    original_total=receipt_meta.get("original_total"),
                    content=receipt_meta.get("content"),
                )
                receipt_id = uuid_hex()
                conn.execute(
                    """
                    INSERT INTO receipt_file (receipt_id, transaction_id, file_type, file_name, file_path, original_total, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt_id,
                        transaction_id,
                        file_type,
                        record["file_name"],
                        record["file_path"],
                        record["original_total"],
                        now,
                    ),
                )

        # 장부 저장을 먼저 확정해야 log_request의 별도 연결과 잠금이 충돌하지 않는다.
        conn.commit()

        log_request(
            "저장",
            STATUS_SUCCESS,
            related_transaction_id=transaction_id,
            related_receipt_id=receipt_id,
        )
        if receipt_id:
            sync_receipt_file_names(transaction_id)

        return {
            "transaction_id": transaction_id,
            "status": status,
            "saved_items": saved_items,
            "saved_allocations": saved_allocations,
            "receipt_id": receipt_id,
        }
    except Exception as e:
        conn.rollback()
        log_request(
            "저장",
            STATUS_PARTIAL if receipt_meta else STATUS_FAILED,
            error_note=str(e),
        )
        raise
    finally:
        conn.close()


ALLOWED_TRANSACTION_STATUSES = ("확정", "임시", "취소")


def set_transaction_status(transaction_id: str, new_status: str) -> Dict[str, Any]:
    """거래 상태를 임시/확정/취소로 바꾼다. 잔액은 '확정' 거래만 반영한다."""
    if new_status not in ALLOWED_TRANSACTION_STATUSES:
        return {"ok": False, "error": "허용되지 않는 상태입니다."}

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM transactions WHERE transaction_id = ?", (transaction_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "거래를 찾을 수 없습니다."}
        if row["status"] == new_status:
            return {"ok": True, "changed": False, "status": new_status}
        if row["status"] == "확정" and conn.execute(
            "SELECT 1 FROM refund WHERE original_transaction_id = ? AND status = '완료'", (transaction_id,)
        ).fetchone():
            return {"ok": False, "error": "환불 내역이 있는 확정 거래는 상태를 바꿀 수 없습니다. 환불을 먼저 취소해 주세요."}

        conn.execute(
            "UPDATE transactions SET status = ?, updated_at = ? WHERE transaction_id = ?",
            (new_status, _now_iso(), transaction_id),
        )
        conn.commit()
    finally:
        conn.close()

    log_request("상태변경", STATUS_SUCCESS, related_transaction_id=transaction_id)
    return {"ok": True, "changed": True, "status": new_status}


def save_cart(cart: Dict[str, Any]) -> str:
    """장바구니 검토 내용을 저장하고 번호(cart_id)를 돌려준다. 주소창 길이 제한을 피하기 위함."""
    import json

    conn = get_connection()
    try:
        cart_id = uuid_hex()
        conn.execute(
            "INSERT INTO cart_proposal (cart_id, payload_json, created_at) VALUES (?, ?, ?)",
            (cart_id, json.dumps(cart, ensure_ascii=False), _now_iso()),
        )
        conn.commit()
        return cart_id
    finally:
        conn.close()


def load_cart(cart_id: str) -> Optional[Dict[str, Any]]:
    import json

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT payload_json FROM cart_proposal WHERE cart_id = ?", (cart_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row["payload_json"])
    except ValueError:
        return None


def get_transaction_detail(transaction_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        tx = conn.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
        if not tx:
            return None
        items = conn.execute(
            "SELECT * FROM item WHERE transaction_id = ? ORDER BY created_at ASC, rowid ASC", (transaction_id,)
        ).fetchall()
        files = conn.execute(
            "SELECT receipt_id, file_type, file_name FROM receipt_file WHERE transaction_id = ? ORDER BY created_at ASC",
            (transaction_id,),
        ).fetchall()
        return {"transaction": dict(tx), "items": [dict(i) for i in items], "receipt_files": [dict(f) for f in files]}
    finally:
        conn.close()


def update_transaction(
    transaction_id: str,
    budget_id: str,
    merchant_name: str,
    transaction_date: str,
    total_amount: float,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """거래 내용을 고친다. 품목은 새 목록으로 통째로 바꾸고, 배정액도 총액에 맞춘다."""
    if not merchant_name.strip():
        return {"ok": False, "error": "거래처를 입력해 주세요."}

    conn = get_connection()
    try:
        tx = conn.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
        if not tx:
            return {"ok": False, "error": "거래를 찾을 수 없습니다."}
        if not conn.execute("SELECT 1 FROM budget WHERE budget_id = ?", (budget_id,)).fetchone():
            return {"ok": False, "error": "예산을 찾을 수 없습니다."}

        refunded = conn.execute(
            "SELECT COALESCE(SUM(refund_amount), 0) AS v FROM refund WHERE original_transaction_id = ? AND status = '완료'",
            (transaction_id,),
        ).fetchone()["v"]
        if refunded > 0 and budget_id != tx["budget_id"]:
            return {"ok": False, "error": "환불 내역이 있는 거래는 예산을 바꿀 수 없습니다. 환불을 먼저 취소해 주세요."}
        if total_amount + 1e-9 < refunded:
            return {"ok": False, "error": f"총액은 이미 환불된 금액({refunded:,.0f}원)보다 작을 수 없습니다."}

        now = _now_iso()
        conn.execute(
            """
            UPDATE transactions
            SET budget_id = ?, merchant_name = ?, transaction_date = ?, total_amount = ?, updated_at = ?
            WHERE transaction_id = ?
            """,
            (budget_id, merchant_name.strip(), transaction_date.strip() or "미확인", total_amount, now, transaction_id),
        )

        conn.execute("DELETE FROM item WHERE transaction_id = ?", (transaction_id,))
        for item in items:
            row = validate_item_row(item)
            if not row["item_name"]:
                continue
            conn.execute(
                """
                INSERT INTO item (item_id, transaction_id, item_name, specification, quantity, unit, unit_price, item_amount, selection_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (uuid_hex(), transaction_id, row["item_name"], row["specification"], row["quantity"],
                 row["unit"] or "개", row["unit_price"], row["item_amount"], row["selection_status"], now, now),
            )

        updated = conn.execute(
            "UPDATE budget_allocation SET budget_id = ?, allocated_amount = ? WHERE transaction_id = ?",
            (budget_id, total_amount, transaction_id),
        ).rowcount
        if not updated:
            conn.execute(
                "INSERT INTO budget_allocation (allocation_id, transaction_id, budget_id, allocated_amount, unallocated_amount, created_at) VALUES (?, ?, ?, ?, NULL, ?)",
                (uuid_hex(), transaction_id, budget_id, total_amount, now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    log_request("거래수정", STATUS_SUCCESS, related_transaction_id=transaction_id)
    sync_receipt_file_names(transaction_id)
    return {"ok": True}


def delete_transaction(transaction_id: str) -> Dict[str, Any]:
    """거래와 딸린 품목·배정·증빙 파일을 완전히 지운다. 확정 거래나 환불 내역이 있는 거래는 막는다."""
    import os

    import app.storage as storage

    conn = get_connection()
    file_paths: List[str] = []
    try:
        tx = conn.execute("SELECT status FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
        if not tx:
            return {"ok": False, "error": "거래를 찾을 수 없습니다."}
        if tx["status"] == "확정":
            return {"ok": False, "error": "확정된 거래는 삭제할 수 없습니다. 먼저 '임시로' 또는 '취소'로 바꿔 주세요."}
        if conn.execute(
            "SELECT 1 FROM refund WHERE original_transaction_id = ? AND status = '완료'", (transaction_id,)
        ).fetchone():
            return {"ok": False, "error": "환불 내역이 있는 거래는 삭제할 수 없습니다. 환불을 먼저 취소해 주세요."}

        receipts = conn.execute(
            "SELECT receipt_id, file_path FROM receipt_file WHERE transaction_id = ?", (transaction_id,)
        ).fetchall()
        file_paths = [r["file_path"] for r in receipts]

        conn.execute("UPDATE request_log SET related_transaction_id = NULL WHERE related_transaction_id = ?", (transaction_id,))
        for r in receipts:
            conn.execute("UPDATE request_log SET related_receipt_id = NULL WHERE related_receipt_id = ?", (r["receipt_id"],))
        conn.execute("DELETE FROM refund WHERE original_transaction_id = ?", (transaction_id,))
        conn.execute("DELETE FROM receipt_file WHERE transaction_id = ?", (transaction_id,))
        conn.execute("DELETE FROM item WHERE transaction_id = ?", (transaction_id,))
        conn.execute("DELETE FROM budget_allocation WHERE transaction_id = ?", (transaction_id,))
        conn.execute("DELETE FROM transactions WHERE transaction_id = ?", (transaction_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # DB 삭제가 끝난 뒤, 증빙 저장 폴더 안의 파일만 지운다.
    root = os.path.realpath(storage.RECEIPTS_DIR)
    for path in file_paths:
        real = os.path.realpath(path)
        if real.startswith(root + os.sep) and os.path.isfile(real):
            try:
                os.remove(real)
            except OSError:
                pass

    log_request("거래삭제", STATUS_SUCCESS)
    return {"ok": True}


def sync_receipt_file_names(transaction_id: str) -> None:
    """거래의 원본 증빙 파일 이름을 '예산명_일자_거래처'에 맞춘다. 파일과 DB를 함께 바꾼다."""
    import os

    import app.storage as storage

    conn = get_connection()
    try:
        tx = conn.execute(
            """
            SELECT t.merchant_name, t.transaction_date, b.budget_name
            FROM transactions t JOIN budget b ON b.budget_id = t.budget_id
            WHERE t.transaction_id = ?
            """,
            (transaction_id,),
        ).fetchone()
        if not tx:
            return
        rows = conn.execute(
            "SELECT receipt_id, file_path, file_name FROM receipt_file WHERE transaction_id = ? AND file_type = '원본' ORDER BY created_at ASC, rowid ASC",
            (transaction_id,),
        ).fetchall()
        root = os.path.realpath(storage.RECEIPTS_DIR)
        for index, row in enumerate(rows, start=1):
            current = os.path.realpath(row["file_path"])
            if not current.startswith(root + os.sep) or not os.path.isfile(current):
                continue
            directory = os.path.dirname(current)
            desired = storage.evidence_file_name(
                tx["budget_name"], tx["transaction_date"], tx["merchant_name"], os.path.splitext(current)[1], number=index
            )
            new_name = storage.unique_name(directory, desired, current_path=current)
            new_path = os.path.join(directory, new_name)
            if new_name == row["file_name"] and os.path.samefile(new_path, current):
                continue
            if not (os.path.exists(new_path) and os.path.samefile(new_path, current)):
                os.rename(current, new_path)
            try:
                conn.execute("UPDATE receipt_file SET file_name = ?, file_path = ? WHERE receipt_id = ?", (new_name, new_path, row["receipt_id"]))
                conn.commit()
            except Exception:
                conn.rollback()
                if os.path.exists(new_path) and not os.path.exists(current):
                    os.rename(new_path, current)
                raise
    finally:
        conn.close()


def sync_budget_receipt_names(budget_id: str) -> None:
    conn = get_connection()
    try:
        ids = [r["transaction_id"] for r in conn.execute("SELECT transaction_id FROM transactions WHERE budget_id = ?", (budget_id,)).fetchall()]
    finally:
        conn.close()
    for tid in ids:
        sync_receipt_file_names(tid)


def refund_overview(transaction_id: str) -> Optional[Dict[str, Any]]:
    """거래의 환불 가능 금액과 환불 이력을 돌려준다. 거래가 없으면 None."""
    conn = get_connection()
    try:
        tx = conn.execute(
            """
            SELECT t.transaction_id, t.transaction_date, t.merchant_name, t.total_amount,
                   t.status, t.budget_id, b.budget_name
            FROM transactions t JOIN budget b ON b.budget_id = t.budget_id
            WHERE t.transaction_id = ?
            """,
            (transaction_id,),
        ).fetchone()
        if not tx:
            return None
        allocated = conn.execute(
            "SELECT COALESCE(SUM(allocated_amount), 0) AS v FROM budget_allocation WHERE transaction_id = ? AND budget_id = ?",
            (transaction_id, tx["budget_id"]),
        ).fetchone()["v"]
        refunds = conn.execute(
            "SELECT * FROM refund WHERE original_transaction_id = ? ORDER BY created_at ASC",
            (transaction_id,),
        ).fetchall()
        done = sum(r["refund_amount"] for r in refunds if r["status"] == "완료")
        return {
            "transaction": dict(tx),
            "allocated_amount": allocated,
            "refunded_amount": done,
            "refundable_amount": max(allocated - done, 0.0),
            "refunds": [dict(r) for r in refunds],
        }
    finally:
        conn.close()


def create_refund(transaction_id: str, refund_date: str, refund_amount: float) -> Dict[str, Any]:
    """확정된 거래에 대한 환불을 기록한다. 환불 합계는 거래 배정액을 넘을 수 없다."""
    overview = refund_overview(transaction_id)
    if overview is None:
        return {"ok": False, "error": "거래를 찾을 수 없습니다."}
    if overview["transaction"]["status"] != "확정":
        return {"ok": False, "error": "확정된 거래만 환불할 수 있습니다."}
    if refund_amount <= 0:
        return {"ok": False, "error": "환불 금액은 0보다 커야 합니다."}
    if refund_amount > overview["refundable_amount"] + 1e-9:
        return {"ok": False, "error": f"환불 가능 금액({overview['refundable_amount']:,.0f}원)을 넘을 수 없습니다."}

    refund_id = uuid_hex()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO refund (refund_id, original_transaction_id, refund_date, refund_amount, budget_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, '완료', ?)
            """,
            (refund_id, transaction_id, refund_date.strip() or "미확인", refund_amount,
             overview["transaction"]["budget_id"], _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    log_request("환불", STATUS_SUCCESS, related_transaction_id=transaction_id)
    return {"ok": True, "refund_id": refund_id}


def cancel_refund(refund_id: str) -> Dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT original_transaction_id, status FROM refund WHERE refund_id = ?", (refund_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "환불 내역을 찾을 수 없습니다."}
        conn.execute("UPDATE refund SET status = '취소' WHERE refund_id = ?", (refund_id,))
        conn.commit()
        return {"ok": True, "transaction_id": row["original_transaction_id"]}
    finally:
        conn.close()


def budget_balance_summary():
    conn = get_connection()
    try:
        budgets = conn.execute("SELECT * FROM budget ORDER BY year DESC, budget_name ASC").fetchall()
        result = []
        for b in budgets:
            spent = conn.execute(
                """
                SELECT COALESCE(SUM(ba.allocated_amount), 0) AS spent
                FROM budget_allocation ba
                JOIN transactions t ON t.transaction_id = ba.transaction_id
                WHERE t.status = '확정' AND ba.budget_id = ?
                """,
                (b["budget_id"],),
            ).fetchone()["spent"]

            refunded = conn.execute(
                """
                SELECT COALESCE(SUM(r.refund_amount), 0) AS refunded
                FROM refund r
                JOIN transactions t ON t.transaction_id = r.original_transaction_id
                WHERE r.budget_id = ? AND r.status = '완료' AND t.status = '확정'
                """,
                (b["budget_id"],),
            ).fetchone()["refunded"]

            result.append({
                "budget_id": b["budget_id"],
                "budget_name": b["budget_name"],
                "year": b["year"],
                "type": b["type"],
                "allocated_amount": b["allocated_amount"],
                "spent": spent,
                "refunded": refunded,
                "balance": b["allocated_amount"] - spent + refunded,
            })
        return result
    finally:
        conn.close()


def expense_ledger(filter_budget_id: str | None = None, status: str | None = None):
    conn = get_connection()
    try:
        query = """
            SELECT t.transaction_id, t.transaction_date, t.merchant_name, t.total_amount, t.status, b.budget_name
            FROM transactions t
            JOIN budget b ON b.budget_id = t.budget_id
            WHERE 1=1
        """
        params = []
        if filter_budget_id:
            query += " AND t.budget_id = ?"
            params.append(filter_budget_id)
        if status:
            query += " AND t.status = ?"
            params.append(status)
        query += " ORDER BY t.transaction_date DESC, t.created_at DESC"

        rows = conn.execute(query, params).fetchall()
        ledger = []
        for r in rows:
            items = conn.execute(
                "SELECT * FROM item WHERE transaction_id = ? ORDER BY created_at ASC",
                (r["transaction_id"],),
            ).fetchall()
            receipt_files = conn.execute(
                "SELECT * FROM receipt_file WHERE transaction_id = ? ORDER BY created_at ASC",
                (r["transaction_id"],),
            ).fetchall()
            refunded = conn.execute(
                "SELECT COALESCE(SUM(refund_amount), 0) AS v FROM refund WHERE original_transaction_id = ? AND status = '완료'",
                (r["transaction_id"],),
            ).fetchone()["v"]
            ledger.append({
                "refunded_amount": refunded,
                "transaction_id": r["transaction_id"],
                "transaction_date": r["transaction_date"],
                "merchant_name": r["merchant_name"],
                "total_amount": r["total_amount"],
                "status": r["status"],
                "budget_name": r["budget_name"],
                "items": [dict(i) for i in items],
                "receipt_files": [dict(f) for f in receipt_files],
            })
        return ledger
    finally:
        conn.close()


def import_draft(draft: Dict[str, Any], selected_budget_id: str, user_confirmed: bool = False) -> Dict[str, Any]:
    """
    인식 초안 또는 수기 초안을 받아 확인 후 SQLite 저장 형식으로 변환한다.
    이 함수는 아직 확정 전 초안을 임시로 다루는 데 사용하며,
    확정 전까지 장부로 자동 전이하지 않는다.
    """
    doc_type = draft.get("document_type")
    if doc_type not in ("receipt", "cart"):
        return {"ok": False, "error": "현재 저장 대상은 영수증/장바구니 초안입니다."}

    review_notes = draft.get("review_notes") or []
    if not user_confirmed:
        review_notes = review_notes + ["사용자 확인 전"]

    merchant_name = str(draft.get("merchant_name") or "").strip()
    date = str(draft.get("date") or draft.get("document_date") or "").strip()
    total_amount = to_float(draft.get("total_amount") or draft.get("expected_total_amount"), 0.0)
    items = []
    for it in (draft.get("items") or []):
        row = validate_item_row(it)
        row["selection_status"] = row.get("selection_status") or "선택"
        items.append(row)

    note = draft.get("note")
    if note:
        review_notes = review_notes + [note]

    if not merchant_name:
        review_notes = review_notes + ["거래처 미확인"]

    if is_empty_number(total_amount):
        total_amount = 0.0
        review_notes = review_notes + ["총액 미확인"]

    if not date:
        review_notes = review_notes + ["사용일 미확인"]

    return {
        "doc_type": doc_type,
        "merchant_name": merchant_name,
        "date": date,
        "total_amount": total_amount,
        "items": items,
        "review_notes": review_notes,
        "bank_ready": bool(user_confirmed and merchant_name and date and not is_empty_number(total_amount)),
    }
