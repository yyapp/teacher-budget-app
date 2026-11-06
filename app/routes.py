# -*- coding: utf-8 -*-
"""
teacher-budget-app / app/routes.py

브라우저에서 쓰는 주요 화면과 저장 흐름을 처리한다.
"""

import mimetypes
import os
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_file, abort

import json
from app.database import get_connection, init_db
from app.upstage_client import parse_receipt_image
from app.forms import InputError, parse_amount, parse_items, read_upload
from app.services import create_budget, list_budgets, update_budget, save_transaction_with_items, budget_balance_summary, expense_ledger, import_draft, set_transaction_status, save_cart, load_cart, refund_overview, create_refund, cancel_refund, get_transaction_detail, update_transaction, delete_transaction, sync_receipt_file_names
from app.status import STATUS_TEMPORARY, STATUS_SUCCESS, STATUS_FAILED, uuid_hex

routes_bp = Blueprint("routes", __name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))


def _require_init():
    try:
        init_db()
    except Exception:
        pass


@routes_bp.route("/")
def index():
    _require_init()
    summary = budget_balance_summary()
    return render_template("index.html", summary=summary)


@routes_bp.route("/budgets")
def budgets():
    _require_init()
    rows = list_budgets()
    return render_template("budgets.html", budgets=rows)


@routes_bp.route("/budgets/create", methods=["GET", "POST"])
def budget_create():
    _require_init()
    if request.method == "POST":
        budget_name = request.form.get("budget_name", "").strip()
        year_text = request.form.get("year", "").strip()
        type_ = request.form.get("type", "확정").strip()
        amount_text = request.form.get("allocated_amount", "").strip()
        applied_date = request.form.get("applied_date", "").strip()

        if not budget_name:
            flash("예산명을 입력해 주세요.", "error")
            return redirect(url_for("routes.budget_create"))
        if not year_text or not year_text.isdigit():
            flash("연도를 숫자로 입력해 주세요.", "error")
            return redirect(url_for("routes.budget_create"))
        try:
            year = int(year_text)
        except ValueError:
            flash("연도를 숫자로 입력해 주세요.", "error")
            return redirect(url_for("routes.budget_create"))

        try:
            allocated_amount = parse_amount(amount_text, "배정액")
        except InputError as e:
            flash(str(e), "error")
            return redirect(url_for("routes.budget_create"))

        create_budget(budget_name, year, type_, allocated_amount, applied_date)
        flash("예산을 등록했습니다.", "success")
        return redirect(url_for("routes.budgets"))

    return render_template("budget_create.html")


@routes_bp.route("/budgets/<budget_id>/edit", methods=["GET", "POST"])
def budget_edit(budget_id):
    _require_init()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM budget WHERE budget_id = ?", (budget_id,)).fetchone()
        if not row:
            flash("예산을 찾을 수 없습니다.", "error")
            return redirect(url_for("routes.budgets"))
    finally:
        conn.close()

    if request.method == "POST":
        budget_name = request.form.get("budget_name", "").strip()
        year_text = request.form.get("year", "").strip()
        type_ = request.form.get("type", "확정").strip()
        amount_text = request.form.get("allocated_amount", "").strip()
        applied_date = request.form.get("applied_date", "").strip()

        if not budget_name:
            flash("예산명을 입력해 주세요.", "error")
            return redirect(url_for("routes.budget_edit", budget_id=budget_id))
        if not year_text or not year_text.isdigit():
            flash("연도를 숫자로 입력해 주세요.", "error")
            return redirect(url_for("routes.budget_edit", budget_id=budget_id))

        year = int(year_text)
        try:
            allocated_amount = parse_amount(amount_text, "배정액")
        except InputError as e:
            flash(str(e), "error")
            return redirect(url_for("routes.budget_edit", budget_id=budget_id))

        result = update_budget(budget_id, budget_name, year, type_, allocated_amount, applied_date)
        if not result.get("ok"):
            flash("예산 수정에 실패했습니다.", "error")
            return redirect(url_for("routes.budget_edit", budget_id=budget_id))

        flash("예산을 수정했습니다. 관련 화면에 바로 반영됩니다.", "success")
        return redirect(url_for("routes.budgets"))

    return render_template("budget_edit.html", budget=dict(row))


_ocr_calls = 0  # 프로그램을 켠 뒤 자동 인식을 부른 횟수 (OCR_MAX_CALLS로 상한을 둘 수 있음)


def _recognize_receipt(file_content: bytes):
    """파일을 자동 인식한다. (인식 결과 dict 또는 None, 사용자에게 보여 줄 오류 문구 또는 None)"""
    global _ocr_calls
    api_key = os.environ.get("UPSTAGE_API_KEY", "").strip()
    if not api_key:
        return None, "자동 인식용 API 키가 설정되지 않았습니다. 아래에서 직접 입력해 주세요."
    limit = os.environ.get("OCR_MAX_CALLS", "").strip()
    if limit.isdigit() and _ocr_calls >= int(limit):
        return None, "시연용 자동 인식 사용 한도에 도달했습니다. 아래에서 직접 입력해 주세요."
    _ocr_calls += 1
    parsed = parse_receipt_image(file_content, api_key)
    if not parsed.get("ok"):
        return None, f"자동 인식에 실패했습니다. 아래에서 직접 입력해 주세요. ({parsed.get('error')})"
    return parsed, None


@routes_bp.route("/upload", methods=["GET", "POST"])
def upload():
    """예산을 고르고 영수증 이미지/PDF를 올리면, 자동 인식 후 확인 화면으로 이동한다."""
    _require_init()
    budgets = list_budgets()

    if request.method == "POST":
        budget_id = request.form.get("budget_id", "").strip()
        budget = next((b for b in budgets if b["budget_id"] == budget_id), None)
        if budget is None:
            flash("예산을 선택해 주세요.", "error")
            return redirect(url_for("routes.upload"))

        try:
            file_content, original_ext = read_upload(request.files.get("source_file"))
        except InputError as e:
            flash(str(e), "error")
            return redirect(url_for("routes.upload"))
        if file_content is None:
            flash("영수증 이미지나 PDF 파일을 선택해 주세요.", "error")
            return redirect(url_for("routes.upload"))

        parsed, ocr_error = _recognize_receipt(file_content)
        items = parsed["items"] if parsed else []
        merchant_name = (parsed["merchant"] if parsed else "") or "미확인 거래처"
        date = (parsed["date"] if parsed else "") or "미확인"
        total_amount = ((parsed["total_amount"] or sum(i["item_amount"] for i in items)) if parsed else 0.0) or 0.0

        result = save_transaction_with_items(
            budget_id=budget_id,
            merchant_name=merchant_name,
            transaction_date=date,
            total_amount=total_amount,
            status="임시",
            items=items,
            allocations=[{"budget_id": budget_id, "allocated_amount": total_amount, "unallocated_amount": None}],
            receipt_meta={
                "file_type": "원본",
                "date": date,
                "budget_name": budget["budget_name"],
                "index": 1,
                "original_ext": original_ext,
                "original_total": total_amount or None,
                "content": file_content,
            },
        )

        if ocr_error:
            flash(ocr_error, "error")
        else:
            flash("영수증을 읽었습니다. 원본과 비교해 확인하고, 고칠 곳이 있으면 고친 뒤 '확정'을 눌러 주세요.", "success")
        return redirect(url_for("routes.transaction_edit", transaction_id=result["transaction_id"], new=1))

    return render_template("upload.html", budgets=budgets)


@routes_bp.route("/ledger")
def ledger():
    _require_init()
    filter_budget = request.args.get("budget_id", "").strip()
    rows = expense_ledger(filter_budget_id=filter_budget or None)
    budgets = list_budgets()
    return render_template("ledger.html", rows=rows, budgets=budgets, filter_budget=filter_budget)


@routes_bp.route("/transactions/<transaction_id>/status", methods=["POST"])
def transaction_status(transaction_id):
    _require_init()
    new_status = request.form.get("status", "").strip()
    result = set_transaction_status(transaction_id, new_status)
    if result.get("ok"):
        flash(f"거래 상태를 '{new_status}'(으)로 바꿨습니다.", "success")
    else:
        flash(result.get("error", "상태 변경에 실패했습니다."), "error")
    return redirect(url_for("routes.ledger", budget_id=request.form.get("budget_id", "")))


@routes_bp.route("/transactions/<transaction_id>/edit", methods=["GET", "POST"])
def transaction_edit(transaction_id):
    """거래 확인·수정 화면. 업로드 직후(?new=1)에는 '인식 결과 확인' 화면으로 쓰고, '확정'까지 할 수 있다."""
    _require_init()
    detail = get_transaction_detail(transaction_id)
    if detail is None:
        flash("거래를 찾을 수 없습니다.", "error")
        return redirect(url_for("routes.ledger"))
    is_new = request.args.get("new") == "1"
    back = url_for("routes.transaction_edit", transaction_id=transaction_id, new=1 if is_new else None)

    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            items = parse_items(request.form)
            total_amount = parse_amount(request.form.get("total_amount"), "총액")
        except InputError as e:
            flash(str(e), "error")
            return redirect(back)
        result = update_transaction(
            transaction_id,
            request.form.get("budget_id", "").strip(),
            request.form.get("merchant_name", ""),
            request.form.get("transaction_date", ""),
            total_amount,
            items,
        )
        if not result.get("ok"):
            flash(result.get("error", "수정에 실패했습니다."), "error")
            return redirect(back)

        if action == "confirm":
            if total_amount <= 0:
                flash("수정 내용은 저장했지만, 총액이 0원이라 확정하지 못했습니다. 총액을 입력한 뒤 다시 확정해 주세요.", "error")
                return redirect(back)
            confirmed = set_transaction_status(transaction_id, "확정")
            if not confirmed.get("ok"):
                flash(confirmed.get("error", "확정에 실패했습니다."), "error")
                return redirect(back)
            flash("거래를 확정했습니다. 예산 잔액에 반영되었습니다.", "success")
            return redirect(url_for("routes.ledger"))

        flash("거래를 저장했습니다. (아직 확정 전이라 잔액에는 반영되지 않습니다.)" if detail["transaction"]["status"] != "확정" else "거래를 수정했습니다.", "success")
        return redirect(url_for("routes.ledger"))

    return render_template("transaction_edit.html", detail=detail, budgets=list_budgets(), is_new=is_new)


@routes_bp.route("/transactions/<transaction_id>/evidence")
def transaction_evidence(transaction_id):
    """증빙서류 출력 화면: 예산명·일자·거래처·품목 요약과 원본 파일을 한 장에 보여 주고 인쇄한다."""
    _require_init()
    overview = refund_overview(transaction_id)
    if overview is None:
        flash("거래를 찾을 수 없습니다.", "error")
        return redirect(url_for("routes.ledger"))
    sync_receipt_file_names(transaction_id)  # 예전에 올린 파일도 규칙에 맞는 이름으로 맞춘다.
    detail = get_transaction_detail(transaction_id)
    return render_template("evidence.html", detail=detail, overview=overview)


@routes_bp.route("/receipts/<receipt_id>/file")
def receipt_file_view(receipt_id):
    """증빙 파일(이미지/PDF)을 보여 준다. 등록된 파일이면서 증빙 저장 폴더 안에 있을 때만."""
    import app.storage as storage

    conn = get_connection()
    try:
        row = conn.execute("SELECT file_path, file_name FROM receipt_file WHERE receipt_id = ?", (receipt_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        abort(404)
    real = os.path.realpath(row["file_path"])
    root = os.path.realpath(storage.RECEIPTS_DIR)
    if not real.startswith(root + os.sep) or not os.path.isfile(real):
        abort(404)
    mimetype = mimetypes.guess_type(real)[0] or "application/octet-stream"
    resp = send_file(real, mimetype=mimetype, as_attachment=request.args.get("download") == "1", download_name=row["file_name"])
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@routes_bp.app_template_filter("money")
def money_filter(value):
    """12000 → 12,000 (증빙서류용 천 단위 쉼표)"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return f"{int(f):,}" if f == int(f) else f"{f:,.2f}".rstrip("0").rstrip(".")


@routes_bp.app_template_filter("num")
def num_filter(value):
    """2000.0 → 2000, 2.5 → 2.5 (화면에 소수점 0이 길게 붙지 않게)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return str(int(f)) if f == int(f) else f"{f:.2f}".rstrip("0").rstrip(".")


@routes_bp.route("/transactions/<transaction_id>/delete", methods=["POST"])
def transaction_delete(transaction_id):
    _require_init()
    result = delete_transaction(transaction_id)
    if result.get("ok"):
        flash("거래를 삭제했습니다.", "success")
    else:
        flash(result.get("error", "삭제에 실패했습니다."), "error")
    return redirect(url_for("routes.ledger"))


@routes_bp.route("/transactions/<transaction_id>/refund", methods=["GET", "POST"])
def refund_new(transaction_id):
    _require_init()
    overview = refund_overview(transaction_id)
    if overview is None:
        flash("거래를 찾을 수 없습니다.", "error")
        return redirect(url_for("routes.ledger"))

    if request.method == "POST":
        try:
            amount = parse_amount(request.form.get("refund_amount"), "환불 금액")
        except InputError as e:
            flash(str(e), "error")
            return redirect(url_for("routes.refund_new", transaction_id=transaction_id))
        result = create_refund(transaction_id, request.form.get("refund_date", ""), amount)
        if result.get("ok"):
            flash("환불을 기록했습니다. 예산 잔액에 바로 반영됩니다.", "success")
            return redirect(url_for("routes.refund_new", transaction_id=transaction_id))
        flash(result.get("error", "환불 기록에 실패했습니다."), "error")
        return redirect(url_for("routes.refund_new", transaction_id=transaction_id))

    return render_template("refund.html", overview=overview)


@routes_bp.route("/refunds/<refund_id>/cancel", methods=["POST"])
def refund_cancel(refund_id):
    _require_init()
    result = cancel_refund(refund_id)
    if not result.get("ok"):
        flash(result.get("error", "환불 취소에 실패했습니다."), "error")
        return redirect(url_for("routes.ledger"))
    flash("환불을 취소했습니다.", "success")
    return redirect(url_for("routes.refund_new", transaction_id=result["transaction_id"]))


@routes_bp.route("/cart-review", methods=["GET", "POST"])
def cart_review():
    _require_init()
    budgets = list_budgets()
    if request.method == "POST":
        merchant_name = (request.form.get("merchant_name") or "").strip()
        document_date = (request.form.get("document_date") or "").strip()
        expected_total_text = (request.form.get("expected_total_amount") or "").strip()
        shipping_fee_text = (request.form.get("shipping_fee") or "").strip()
        discount_terms = (request.form.get("discount_terms") or "").strip()

        try:
            items = parse_items(request.form)
            expected_total = parse_amount(expected_total_text, "예상 총액")
            shipping_fee = parse_amount(shipping_fee_text, "배송비")
        except InputError as e:
            flash(str(e), "error")
            return redirect(url_for("routes.cart_review"))

        cart = {
            "merchant_name": merchant_name or "미확인",
            "document_date": document_date or "미확인",
            "expected_total_amount": expected_total,
            "shipping_fee": shipping_fee,
            "discount_terms": discount_terms,
            "items": items,
        }
        return redirect(url_for("routes.cart_proposal", id=save_cart(cart)))

    return render_template("cart_review.html", budgets=budgets)


@routes_bp.route("/draft-review", methods=["GET", "POST"])
def draft_review():
    _require_init()
    budgets = list_budgets()
    if request.method == "POST":
        draft_json = request.form.get("draft_json")
        draft = json.loads(draft_json) if draft_json else {}
        result = import_draft(draft, selected_budget_id="", user_confirmed=False)
        return render_template("draft_review.html", draft=draft, result=result, budgets=budgets)

    return render_template("draft_review.html", draft={}, result=None, budgets=budgets)


@routes_bp.route("/manual-entry", methods=["GET", "POST"])
def manual_entry():
    _require_init()
    budgets = list_budgets()
    if request.method == "POST":
        budget_id = request.form.get("budget_id", "").strip()
        merchant_name = request.form.get("merchant_name", "").strip()
        transaction_date = request.form.get("transaction_date", "").strip()
        total_amount_text = request.form.get("total_amount", "").strip()

        if not budget_id:
            flash("예산을 선택해 주세요.", "error")
            return redirect(url_for("routes.manual_entry"))
        if not merchant_name:
            flash("거래처를 입력해 주세요.", "error")
            return redirect(url_for("routes.manual_entry"))

        try:
            total_amount = parse_amount(total_amount_text, "총액")
            items = parse_items(request.form)
        except InputError as e:
            flash(str(e), "error")
            return redirect(url_for("routes.manual_entry"))

        result = save_transaction_with_items(
            budget_id=budget_id,
            merchant_name=merchant_name,
            transaction_date=transaction_date or "미확인",
            total_amount=total_amount,
            status="임시",
            items=items,
            allocations=[{"budget_id": budget_id, "allocated_amount": total_amount, "unallocated_amount": None}],
        )

        flash("수기 입력이 저장됐습니다. 내용을 확인한 뒤 확정해 주세요.", "success")
        return redirect(url_for("routes.ledger"))

    return render_template("manual_entry.html", budgets=budgets)


@routes_bp.route("/api/balance")
def api_balance():
    _require_init()
    return jsonify(budget_balance_summary())


@routes_bp.route("/api/ledger")
def api_ledger():
    _require_init()
    filter_budget = request.args.get("budget_id", "").strip()
    return jsonify(expense_ledger(filter_budget_id=filter_budget or None))


@routes_bp.route("/cart-proposal")
def cart_proposal():
    _require_init()
    budgets = list_budgets()
    cart_id = request.args.get("id", "").strip()
    cart = load_cart(cart_id) if cart_id else None
    if cart is None:
        # 예전 주소(?cart=<JSON>)도 계속 열리도록 유지
        raw = request.args.get("cart")
        try:
            cart = json.loads(raw) if raw else {}
        except ValueError:
            cart = {}
        if not isinstance(cart, dict):
            cart = {}
        if cart_id:
            flash("저장된 장바구니를 찾을 수 없습니다.", "error")

    proposal_text = ""
    proposal_json = ""
    if cart.get("items"):
        lines = []
        lines.append(f"거래처: {cart.get('merchant_name') or '미확인'}")
        lines.append(f"문서 날짜: {cart.get('document_date') or '미확인'}")
        lines.append(f"예상 총액: {cart.get('expected_total_amount', 0):,.0f}원")
        if cart.get("shipping_fee"):
            lines.append(f"배송비: {cart.get('shipping_fee', 0):,.0f}원")
        if cart.get("discount_terms"):
            lines.append(f"할인 조건: {cart.get('discount_terms')}")
        lines.append("")
        lines.append("품목 목록")
        total = 0.0
        for it in cart.get("items", []):
            qty = it.get("quantity", 0)
            unit_price = it.get("unit_price", 0)
            item_amount = it.get("item_amount", 0)
            total += item_amount
            spec = it.get("specification")
            spec_str = f" ({spec})" if spec else ""
            lines.append(f"- {it.get('item_name')}{spec_str}")
            lines.append(f"  수량: {qty} {it.get('unit', '개')}")
            lines.append(f"  단가: {unit_price:,.0f}원")
            lines.append(f"  금액: {item_amount:,.0f}원")
        lines.append("")
        lines.append(f"품목 합계: {total:,.0f}원")
        lines.append(f"최종 예상 금액: {cart.get('expected_total_amount', total):,.0f}원")
        lines.append("")
        lines.append("참고: 장바구니 자료로, 결제 증빙이 아닙니다. 품의 작성 시 실제 구매 예정 품목만 포함하세요.")
        proposal_text = "\n".join(lines)

        proposal_json = json.dumps({
            "document_type": "cart",
            "merchant_name": cart.get("merchant_name"),
            "document_date": cart.get("document_date"),
            "expected_total_amount": cart.get("expected_total_amount"),
            "shipping_fee": cart.get("shipping_fee"),
            "discount_terms": cart.get("discount_terms"),
            "items": cart.get("items", []),
        }, ensure_ascii=False, indent=2)

    return render_template("cart_proposal.html", cart=cart, proposal_text=proposal_text, proposal_json=proposal_json, budgets=budgets)
