"""모든 Flask 라우트. 비즈니스 로직은 services.py에 위임."""

import json
import os
import re
import uuid
from datetime import date
from functools import wraps
from typing import Any, Callable, Optional

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from . import services
from .storage import delete_receipt as storage_delete
from .storage import signed_receipt_url
from .storage import upload_receipt as storage_upload
from .receipt_ocr import (
    ReceiptOcrError,
    cart_items_from_ocr,
    parse_cart_image,
    parse_receipt,
)
from .forms import (
    is_valid_amount,
    is_valid_quantity,
    safe_float,
    safe_int,
    non_empty_str,
)

blueprint = Blueprint("app", __name__, template_folder="templates")


def _evidence_filename(
    budget_name: str, receipt_date: str, vendor: str, extension: str
) -> str:
    """Build a predictable, safe evidence filename."""
    parts = []
    for value in (budget_name, receipt_date, vendor):
        cleaned = re.sub(r"[^\w가-힣-]+", "_", value, flags=re.UNICODE).strip("_")
        parts.append(cleaned[:64] or "미상")
    return "_".join(parts) + f".{extension.lower()}"


def _normalize_budget_name(name: str) -> str:
    normalized = re.sub(r"[\s·_-]+", "", name).casefold()
    return re.sub(r"^(20\d{2})(?:년)?(?:학년도)?", r"\1", normalized)


def _short_cart_description(items: list[dict[str, Any]]) -> str:
    names = " ".join(item.get("description", "") for item in items)
    if re.search(r"과자|캔디|초콜릿|초코|사탕|스낵|쿠키|젤리", names):
        return "과자·간식류 구매"
    if re.search(r"수첩|공책|노트|펜|연필|문구|교재|교과서", names):
        return "문구류 구매"
    if re.search(r"모자|분장|장식|풍선", names):
        return "행사 용품 구매"
    return f"장바구니 구매 ({len(items)}개 품목)"


def _clear_pending_ocr_reviews() -> None:
    """Discard unsaved OCR reviews and their temporary receipt uploads."""
    pending = session.pop("pending_receipt", None) or {}
    session.pop("cart_image_review", None)
    for path in (pending.get("receipt_path"), pending.get("additional_document_path")):
        if path:
            try:
                storage_delete(path)
            except Exception:
                pass


# --------------
# 인증
# --------------


def check_access(fn: Callable) -> Callable:
    """간단 비밀번호 인증. ACCESS_PASSWORD가 없으면 인증 없이 통과."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        password = os.environ.get("ACCESS_PASSWORD", "")
        if not password:
            return fn(*args, **kwargs)
        if session.get("authenticated"):
            return fn(*args, **kwargs)
        if request.method == "POST" and request.form.get("password") == password:
            session["authenticated"] = True
            return redirect(request.path)
        return render_template("login.html", next=request.path)

    return wrapper


def logout() -> Response:
    session.pop("authenticated", None)
    return redirect(url_for("app.index"))


# --------------
# 헬퍼
# --------------

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf", "heic", "heif"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _parse_cart_items(form_items: Any) -> list:
    """장바구니 폼에서 품목 목록을 파싱."""
    items = []
    if form_items:
        # form_items가 JSON 문자열이면 디코딩
        if isinstance(form_items, str):
            import json

            try:
                form_items = json.loads(form_items)
            except Exception:
                return []
        if isinstance(form_items, list):
            for idx, item in enumerate(form_items):
                desc = non_empty_str(
                    item.get("description") if isinstance(item, dict) else item
                )
                qty = safe_int(item.get("quantity") if isinstance(item, dict) else 1)
                price = safe_float(item.get("price") if isinstance(item, dict) else 0)
                if desc:
                    items.append(
                        {
                            "description": desc,
                            "quantity": qty if qty >= 1 else 1,
                            "unit_price": price,
                            "amount": price * qty,
                        }
                    )
    return items


# --------------
# 라우트
# --------------


@blueprint.route("/")
@check_access
def index() -> str:
    """홈/대시보드."""
    budgets = services.list_budgets()
    for budget in budgets:
        budget["balance"] = services.get_budget_balance(budget["id"]) or 0
    recent_tx = services.list_transactions(limit=10)
    return render_template("index.html", budgets=budgets, recent_transactions=recent_tx)


@blueprint.route("/budgets")
@check_access
def budgets() -> str:
    """예산 목록 페이지."""
    budgets = services.list_budgets()
    for budget in budgets:
        budget["balance"] = services.get_budget_balance(budget["id"]) or 0
    return render_template("budgets.html", budgets=budgets)


@blueprint.route("/budgets/new", methods=["GET", "POST"])
@check_access
def budget_new() -> Any:
    if request.method == "POST":
        name = non_empty_str(request.form.get("name"))
        amount = safe_float(request.form.get("amount"))
        category = non_empty_str(request.form.get("category"))
        if not name:
            flash("예산 이름을 입력해 주세요.", "error")
            return redirect(url_for("app.budget_new"))
        if any(
            _normalize_budget_name(item["name"]) == _normalize_budget_name(name)
            for item in services.list_budgets()
        ):
            flash(
                "같거나 비슷한 이름의 예산이 이미 있습니다. 기존 예산을 선택해 주세요.",
                "error",
            )
            return render_template("budget_form.html", budget=None, action="등록")
        if not is_valid_amount(amount):
            flash("예산 금액은 0 이상의 숫자를 입력해 주세요.", "error")
            return redirect(url_for("app.budget_new"))
        svc = services.create_budget(name, amount, category)
        if svc:
            flash("예산이 등록되었습니다.", "success")
            return redirect(url_for("app.budgets"))
        flash("예산 등록에 실패했습니다.", "error")
    return render_template("budget_form.html", budget=None, action="등록")


@blueprint.route("/budgets/<budget_id>/edit", methods=["GET", "POST"])
@check_access
def budget_edit(budget_id: str) -> Any:
    budget = services.get_budget(budget_id)
    if not budget:
        abort(404)
    if request.method == "POST":
        name = non_empty_str(request.form.get("name", budget["name"]))
        amount = safe_float(request.form.get("amount", budget["amount"]))
        category = non_empty_str(
            request.form.get("category", budget.get("category", ""))
        )
        if not name:
            flash("예산 이름을 입력해 주세요.", "error")
            return redirect(url_for("app.budget_edit", budget_id=budget_id))
        if any(
            item["id"] != budget_id
            and _normalize_budget_name(item["name"]) == _normalize_budget_name(name)
            for item in services.list_budgets()
        ):
            flash(
                "같거나 비슷한 이름의 예산이 이미 있습니다. 다른 이름을 입력해 주세요.",
                "error",
            )
            return render_template("budget_form.html", budget=budget, action="수정")
        if not is_valid_amount(amount):
            flash("예산 금액은 0 이상의 숫자를 입력해 주세요.", "error")
            return redirect(url_for("app.budget_edit", budget_id=budget_id))
        ok = services.update_budget(budget_id, name, amount, category)
        if ok:
            flash("예산이 수정되었습니다.", "success")
            return redirect(url_for("app.budgets"))
        flash("수정에 실패했습니다.", "error")
    return render_template("budget_form.html", budget=budget, action="수정")


@blueprint.route("/budgets/<budget_id>/delete", methods=["POST"])
@check_access
def budget_delete(budget_id: str) -> Response:
    ok = services.delete_budget(budget_id)
    if ok:
        flash("예산이 삭제되었습니다.", "success")
    else:
        flash("삭제 실패했습니다.", "error")
    return redirect(url_for("app.budgets"))


@blueprint.route("/upload", methods=["GET", "POST"])
@check_access
def upload() -> Any:
    """영수증 업로드 페이지."""
    budgets = services.list_budgets()
    if request.method == "POST":
        action = request.form.get("action", "parse")
        pending = session.get("pending_receipt")

        if action == "cancel":
            session.pop("pending_receipt", None)
            return redirect(url_for("app.upload"))

        if action == "save":
            if not pending:
                flash("영수증을 먼저 업로드해 주세요.", "error")
                return redirect(url_for("app.upload"))
            descriptions = request.form.getlist("item_description")
            quantities = request.form.getlist("item_quantity")
            unit_prices = request.form.getlist("item_unit_price")
            amounts = request.form.getlist("item_amount")
            line_items = []
            for description, quantity, unit_price, amount in zip(
                descriptions, quantities, unit_prices, amounts
            ):
                description = non_empty_str(description)
                quantity_value = safe_int(quantity)
                unit_price_value = safe_float(unit_price)
                amount_value = safe_float(amount)
                if description and quantity_value > 0 and unit_price_value > 0:
                    if amount_value <= 0:
                        amount_value = unit_price_value * quantity_value
                    line_items.append(
                        {
                            "description": description,
                            "quantity": quantity_value,
                            "unit_price": unit_price_value,
                            "amount": amount_value,
                        }
                    )
            if not line_items:
                flash(
                    "영수증에서 품목을 읽지 못했습니다. 품목이 보이도록 다시 촬영해 주세요.",
                    "error",
                )
                return render_template(
                    "upload.html",
                    budgets=budgets,
                    review=pending,
                    values=request.form,
                )
            description = ", ".join(item["description"] for item in line_items)
            amount = safe_float(request.form.get("receipt_total"))
            if amount <= 0:
                flash(
                    "영수증의 실제 결제액을 확인하거나 입력해 주세요. 품목 합계로 대신 저장하지 않습니다.",
                    "error",
                )
                return render_template(
                    "upload.html", budgets=budgets, review=pending, values=request.form
                )
            quantity = sum(item["quantity"] for item in line_items)
            try:
                tx = services.create_transaction(
                    budget_id=pending["budget_id"],
                    description=description,
                    amount=amount,
                    quantity=quantity,
                    unit_price=safe_float(request.form.get("unit_price")) or None,
                    receipt_path=pending["receipt_path"],
                    receipt_filename=pending["receipt_filename"],
                    additional_document_path=pending.get("additional_document_path"),
                    additional_document_filename=pending.get(
                        "additional_document_filename"
                    ),
                    line_items=line_items,
                )
            except Exception:
                flash(
                    "장부 저장에 실패했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.",
                    "error",
                )
                return render_template("upload.html", budgets=budgets, review=pending)
            if tx:
                session.pop("pending_receipt", None)
                flash("영수증 판독 결과로 거래를 등록했습니다.", "success")
                return redirect(url_for("app.ledger"))
            flash("거래 등록에 실패했습니다. 다시 저장해 주세요.", "error")
            return render_template("upload.html", budgets=budgets, review=pending)

        budget_id = non_empty_str(request.form.get("budget_id"))
        file = request.files.get("receipt_file")
        if not budget_id:
            flash("예산을 선택해 주세요.", "error")
            return redirect(url_for("app.upload"))
        if not file or not file.filename:
            flash("판독할 영수증 파일을 선택해 주세요.", "error")
            return redirect(url_for("app.upload"))
        if not allowed_file(file.filename):
            flash("png, jpg, jpeg, heic, pdf 파일을 올려 주세요.", "error")
            return redirect(url_for("app.upload"))
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        if size > MAX_FILE_SIZE:
            flash("파일 크기가 너무 큽니다. (최대 10MB)", "error")
            return redirect(url_for("app.upload"))

        file_bytes = file.read()
        ext = file.filename.rsplit(".", 1)[1].lower()
        budget_name = next(
            (budget["name"] for budget in budgets if budget["id"] == budget_id),
            "예산미상",
        )
        content_type = (
            f"image/{ext}"
            if ext in ("png", "jpg", "jpeg", "heic", "heif")
            else "application/pdf"
        )
        try:
            fields = parse_receipt(file_bytes, file.filename, content_type)
        except ReceiptOcrError as exc:
            flash(str(exc), "error")
            return redirect(url_for("app.upload"))

        receipt_date = fields.get("receipt_date") or date.today().isoformat()
        vendor = fields.get("vendor") or "거래처미상"
        filename = services.unique_evidence_filename(
            _evidence_filename(budget_name, receipt_date, vendor, ext)
        )

        receipt_path = storage_upload(file_bytes, filename, content_type)
        if not receipt_path:
            flash(
                "영수증 저장에 실패했습니다. Supabase의 비공개 'receipts' 저장소 설정을 확인해 주세요.",
                "error",
            )
            return redirect(url_for("app.upload"))

        additional_document_path = None
        additional_document_filename = None
        additional_file = request.files.get("additional_document")
        if additional_file and additional_file.filename:
            if not allowed_file(additional_file.filename):
                storage_delete(receipt_path)
                flash(
                    "추가 서류는 png, jpg, jpeg, heic, pdf 파일만 올릴 수 있습니다.",
                    "error",
                )
                return redirect(url_for("app.upload"))
            additional_file.seek(0, os.SEEK_END)
            additional_size = additional_file.tell()
            additional_file.seek(0)
            if additional_size > MAX_FILE_SIZE:
                storage_delete(receipt_path)
                flash("추가 서류가 너무 큽니다. (최대 10MB)", "error")
                return redirect(url_for("app.upload"))
            additional_bytes = additional_file.read()
            additional_ext = additional_file.filename.rsplit(".", 1)[1].lower()
            additional_document_filename = _evidence_filename(
                budget_name, receipt_date, vendor, additional_ext
            )
            additional_document_filename = services.unique_evidence_filename(
                additional_document_filename, reserved=[filename]
            )
            additional_content_type = (
                f"image/{additional_ext}"
                if additional_ext in ("png", "jpg", "jpeg", "heic", "heif")
                else "application/pdf"
            )
            additional_document_path = storage_upload(
                additional_bytes, additional_document_filename, additional_content_type
            )
            if not additional_document_path:
                storage_delete(receipt_path)
                flash(
                    "추가 서류 저장에 실패했습니다. 파일을 확인하고 다시 시도해 주세요.",
                    "error",
                )
                return redirect(url_for("app.upload"))

        pending = {
            "budget_id": budget_id,
            "receipt_path": receipt_path,
            "receipt_filename": filename,
            "additional_document_path": additional_document_path,
            "additional_document_filename": additional_document_filename,
            **{key: value for key, value in fields.items() if key != "raw_text"},
        }
        pending.setdefault("items", [])
        if not pending["items"]:
            session["pending_receipt"] = pending
            _clear_pending_ocr_reviews()
            flash(
                "영수증 품목을 읽지 못해 임시 파일과 파싱 결과를 지웠습니다. 더 선명한 이미지로 다시 올려 주세요.",
                "error",
            )
            return redirect(url_for("app.upload"))
        pending["items_total"] = sum(
            item["amount"]
            for item in pending["items"]
            if isinstance(item.get("amount"), (int, float))
        )
        has_unresolved_prices = any(
            not isinstance(item.get("amount"), (int, float))
            for item in pending["items"]
        )
        pending["total_difference"] = (
            pending["amount"] - pending["items_total"]
            if pending["amount"] not in (None, "") and not has_unresolved_prices
            else None
        )
        session["pending_receipt"] = pending
        return render_template("upload.html", budgets=budgets, review=pending)

    return render_template(
        "upload.html", budgets=budgets, review=session.get("pending_receipt")
    )


@blueprint.route("/ledger")
@check_access
def ledger() -> str:
    """거래 장부 페이지."""
    budget_id = request.args.get("budget_id", "")
    status = request.args.get("status", "")
    budgets = services.list_budgets()
    transactions = services.list_transactions(
        budget_id=budget_id if budget_id else None,
        status=status if status else None,
    )
    for transaction in transactions:
        transaction["has_evidence"] = bool(
            transaction.get("receipt_path")
            or transaction.get("additional_document_path")
        )
    return render_template(
        "ledger.html",
        transactions=transactions,
        budgets=budgets,
        selected_budget=budget_id,
        selected_status=status,
    )


@blueprint.route("/transactions/<tx_id>")
@check_access
def transaction_detail(tx_id: str) -> str:
    """거래 상세 페이지."""
    tx = services.get_transaction(tx_id)
    if not tx:
        abort(404)
    tx["receipt_url"] = signed_receipt_url(tx.get("receipt_path"))
    tx["additional_document_url"] = signed_receipt_url(
        tx.get("additional_document_path")
    )
    line_items = tx.get("line_items") or []
    if isinstance(line_items, list) and line_items:
        items_total = sum(float(item.get("amount") or 0) for item in line_items)
        cart_summary = tx.get("cart_summary")
        if cart_summary:
            source_subtotal = cart_summary.get("products_subtotal")
            cart_summary["saved_items_subtotal"] = items_total
            cart_summary["saved_products_status"] = (
                "unavailable"
                if source_subtotal is None
                else "matched"
                if source_subtotal == items_total
                else "mismatch"
            )
            tx["cart_check"] = cart_summary
        else:
            tx["receipt_check"] = {
                "items_total": items_total,
                "receipt_total": float(tx.get("amount") or 0),
                "difference": float(tx.get("amount") or 0) - items_total,
                "verified": False,
            }
    return render_template("transaction_detail.html", transaction=tx)


@blueprint.route("/transactions/<tx_id>/confirm", methods=["POST"])
@check_access
def transaction_confirm(tx_id: str) -> Response:
    ok = services.confirm_transaction(tx_id)
    if ok:
        flash("거래가 확정되었습니다.", "success")
    else:
        flash("확정 실패했습니다. (이미 확정됐거나 취소 상태일 수 있음)", "error")
    return redirect(url_for("app.ledger"))


@blueprint.route("/transactions/<tx_id>/cancel", methods=["POST"])
@check_access
def transaction_cancel(tx_id: str) -> Response:
    ok = services.cancel_transaction(tx_id)
    if ok:
        flash("거래가 취소되었습니다.", "success")
    else:
        flash("취소 실패했습니다.", "error")
    return redirect(url_for("app.ledger"))


@blueprint.route("/transactions/<tx_id>/refund", methods=["GET", "POST"])
@check_access
def transaction_refund(tx_id: str) -> Any:
    """환불 처리 페이지."""
    tx = services.get_transaction(tx_id)
    if not tx:
        abort(404)
    if request.method == "POST":
        refund_amount = safe_float(request.form.get("refund_amount"))
        reason = non_empty_str(request.form.get("reason"))
        if not is_valid_amount(refund_amount):
            flash("환불 금액은 0 이상의 숫자를 입력해 주세요.", "error")
            return redirect(url_for("app.transaction_refund", tx_id=tx_id))
        ok = services.refund_transaction(tx_id, refund_amount, reason)
        if ok:
            flash("환불 처리되었습니다.", "success")
            return redirect(url_for("app.ledger"))
        flash("환불 실패했습니다.", "error")
    return render_template("refund.html", transaction=tx)


@blueprint.route("/transactions/<tx_id>/edit", methods=["GET", "POST"])
@check_access
def transaction_edit(tx_id: str) -> Any:
    tx = services.get_transaction(tx_id)
    if not tx:
        abort(404)
    if request.method == "POST":
        description = non_empty_str(request.form.get("description", tx["description"]))
        amount = safe_float(request.form.get("amount", tx["amount"]))
        category = non_empty_str(request.form.get("category", tx.get("category", "")))
        quantity = safe_int(request.form.get("quantity", tx.get("quantity", 1)))
        unit_price = safe_float(request.form.get("unit_price", tx.get("unit_price")))
        notes = non_empty_str(request.form.get("notes", tx.get("notes", "")))
        if not description:
            flash("설명을 입력해 주세요.", "error")
            return redirect(url_for("app.transaction_edit", tx_id=tx_id))
        if not is_valid_amount(amount):
            flash("금액은 0 이상의 숫자를 입력해 주세요.", "error")
            return redirect(url_for("app.transaction_edit", tx_id=tx_id))
        ok = services.update_transaction(
            tx_id,
            description=description,
            amount=amount,
            category=category,
            quantity=quantity,
            unit_price=unit_price if unit_price else None,
            notes=notes,
        )
        if ok:
            flash("거래가 수정되었습니다.", "success")
            return redirect(url_for("app.ledger"))
        flash("수정에 실패했습니다.", "error")
    return render_template("transaction_form.html", transaction=tx, action="수정")


@blueprint.route("/transactions/<tx_id>/delete", methods=["POST"])
@check_access
def transaction_delete(tx_id: str) -> Response:
    ok = services.delete_transaction(tx_id)
    if ok:
        flash("거래가 삭제되었습니다.", "success")
    else:
        flash("삭제할 수 없습니다. (확정·취소·환불된 거래는 삭제 불가)", "error")
    return redirect(url_for("app.ledger"))


@blueprint.route("/cart-review", methods=["GET", "POST"])
@check_access
def cart_review() -> Any:
    """장바구니 이미지를 읽고 품목을 검토한 뒤 장부에 등록한다."""
    budgets = services.list_budgets()
    if request.method == "POST":
        action = request.form.get("action", "parse")
        review = session.get("cart_image_review")
        if action == "cancel":
            session.pop("cart_image_review", None)
            return redirect(url_for("app.cart_review"))

        if action == "parse":
            budget_id = non_empty_str(request.form.get("budget_id"))
            image = request.files.get("cart_image")
            if not budget_id:
                flash("예산을 선택해 주세요.", "error")
                return redirect(url_for("app.cart_review"))
            if not image or not image.filename or not allowed_file(image.filename):
                flash("장바구니 화면 이미지(PNG, JPG)나 PDF를 선택해 주세요.", "error")
                return redirect(url_for("app.cart_review"))
            image.seek(0, os.SEEK_END)
            size = image.tell()
            image.seek(0)
            if size > MAX_FILE_SIZE:
                flash("이미지 크기는 10MB 이하여야 합니다.", "error")
                return redirect(url_for("app.cart_review"))
            extension = image.filename.rsplit(".", 1)[1].lower()
            content_type = (
                f"image/{extension}"
                if extension in ("png", "jpg", "jpeg", "heic", "heif")
                else "application/pdf"
            )
            try:
                fields = parse_cart_image(image.read(), image.filename, content_type)
            except ReceiptOcrError as exc:
                flash(str(exc), "error")
                return redirect(url_for("app.cart_review"))
            items = cart_items_from_ocr(fields.get("items") or [])
            if not items:
                session.pop("cart_image_review", None)
                flash(
                    "장바구니에서 품목을 찾지 못했습니다. 이미지가 선명한지 확인해 주세요.",
                    "error",
                )
                return redirect(url_for("app.cart_review"))
            line_total = sum(
                item["amount"]
                for item in items
                if isinstance(item.get("amount"), (int, float))
            )
            summary = {
                key: fields.get(key)
                for key in (
                    "products_subtotal",
                    "shipping",
                    "discount",
                    "payable",
                    "calculated_items_subtotal",
                    "calculated_payable",
                    "products_status",
                    "payable_status",
                    "discount_seen",
                    "discount_inferred_zero",
                    "summary_seen",
                )
            }
            payable = fields.get("payable")
            if payable is not None:
                suggested_total = payable
                total_source = "image"
            elif fields.get("calculated_payable") is not None:
                suggested_total = fields["calculated_payable"]
                total_source = "calculated"
            elif fields.get("products_subtotal") is not None:
                suggested_total = fields["products_subtotal"]
                total_source = "products_subtotal_only"
            else:
                suggested_total = (
                    line_total
                    if all(
                        isinstance(item.get("amount"), (int, float)) for item in items
                    )
                    else None
                )
                total_source = "items_estimate"
            review = {
                "budget_id": budget_id,
                "items": items,
                "receipt_total": suggested_total,
                "total_source": total_source,
                "items_total": line_total,
                "cart_summary": summary,
                "notes": non_empty_str(request.form.get("notes")),
                "parser_version": 4,
            }
            session["cart_image_review"] = review
            return render_template("cart_review.html", budgets=budgets, review=review)

        if action not in ("requisition", "purchase") or not review:
            flash("장바구니 이미지를 먼저 올려 주세요.", "error")
            return redirect(url_for("app.cart_review"))

        descriptions = request.form.getlist("item_description")
        options = request.form.getlist("item_option")
        package_sizes = request.form.getlist("item_package_size")
        source_texts = request.form.getlist("item_source_text")
        order_quantities = request.form.getlist("item_order_quantity")
        item_amounts = request.form.getlist("item_amount")
        line_items = []
        for index, (
            description,
            option,
            package_size,
            source_text,
            order_quantity,
            amount,
        ) in enumerate(
            zip(
                descriptions,
                options,
                package_sizes,
                source_texts,
                order_quantities,
                item_amounts,
            )
        ):
            description = non_empty_str(description)
            order_quantity_value = safe_int(order_quantity)
            amount_value = safe_float(amount)
            if description and order_quantity_value > 0 and amount_value > 0:
                line_items.append(
                    {
                        "description": description,
                        "option": non_empty_str(option),
                        "package_size": non_empty_str(package_size),
                        "quantity": 1,
                        "order_quantity": order_quantity_value,
                        "unit_price": amount_value / order_quantity_value,
                        "amount": amount_value,
                        "source_text": source_text,
                    }
                )
        if (
            not line_items
            or len(line_items) != len(descriptions)
            or len(descriptions) != len(options)
            or len(options) != len(package_sizes)
            or len(package_sizes) != len(source_texts)
            or len(source_texts) != len(order_quantities)
            or len(descriptions) != len(order_quantities)
            or len(order_quantities) != len(item_amounts)
        ):
            flash("품목의 이름·주문 수량·품목 금액을 확인해 주세요.", "error")
            return render_template("cart_review.html", budgets=budgets, review=review)

        receipt_file = request.files.get("purchase_receipt")
        statement_file = request.files.get("purchase_statement")
        if action == "purchase" and (not receipt_file or not receipt_file.filename):
            flash("구매 완료 처리에는 영수증을 첨부해 주세요.", "error")
            return render_template("cart_review.html", budgets=budgets, review=review)

        item_total = sum(item["amount"] for item in line_items)
        summary = review.get("cart_summary", {})
        cart_total = safe_float(review.get("receipt_total"))
        if cart_total <= 0:
            cart_total = safe_float(summary.get("calculated_payable"))
        if cart_total <= 0:
            cart_total = safe_float(summary.get("products_subtotal"))
        if cart_total <= 0:
            cart_total = item_total
        if cart_total <= 0:
            flash("품목 금액을 읽지 못했습니다. 품목을 확인해 주세요.", "error")
            return render_template("cart_review.html", budgets=budgets, review=review)
        source_subtotal = summary.get("products_subtotal")
        if source_subtotal is not None and item_total != source_subtotal:
            flash(
                f"원문 상품 합계 {source_subtotal:,.0f}원과 품목 목록 합계 {item_total:,.0f}원이 다릅니다. 품목 내역을 확인해 주세요.",
                "error",
            )
            return render_template("cart_review.html", budgets=budgets, review=review)
        source_payable = summary.get("payable")
        calculated_payable = summary.get("calculated_payable")
        if (
            source_payable is not None
            and calculated_payable is not None
            and source_payable != calculated_payable
        ):
            flash(
                "원문 결제 예정 금액이 상품 합계·배송비·할인 계산과 맞지 않습니다. 주문 요약을 확인해 주세요.",
                "error",
            )
            return render_template("cart_review.html", budgets=budgets, review=review)

        budget_name = next(
            (
                budget["name"]
                for budget in budgets
                if budget["id"] == review["budget_id"]
            ),
            "예산미상",
        )
        evidence = {}
        for field, uploaded in (
            ("receipt", receipt_file),
            ("statement", statement_file),
        ):
            if not uploaded or not uploaded.filename:
                continue
            if not allowed_file(uploaded.filename):
                for saved in evidence.values():
                    storage_delete(saved["path"])
                flash(
                    "증빙 파일은 PNG, JPG, JPEG, HEIC, PDF만 올릴 수 있습니다.", "error"
                )
                return render_template(
                    "cart_review.html", budgets=budgets, review=review
                )
            uploaded.seek(0, os.SEEK_END)
            file_size = uploaded.tell()
            uploaded.seek(0)
            if file_size > MAX_FILE_SIZE:
                for saved in evidence.values():
                    storage_delete(saved["path"])
                flash("각 증빙 파일은 10MB 이하여야 합니다.", "error")
                return render_template(
                    "cart_review.html", budgets=budgets, review=review
                )
            extension = uploaded.filename.rsplit(".", 1)[1].lower()
            content_type = (
                f"image/{extension}"
                if extension in ("png", "jpg", "jpeg", "heic", "heif")
                else "application/pdf"
            )
            base_name = _evidence_filename(
                budget_name,
                date.today().isoformat(),
                "영수증" if field == "receipt" else "내역서",
                extension,
            )
            stored_name = services.unique_evidence_filename(base_name)
            path = storage_upload(uploaded.read(), stored_name, content_type)
            if not path:
                for saved in evidence.values():
                    storage_delete(saved["path"])
                flash("증빙 파일 저장에 실패했습니다. 다시 시도해 주세요.", "error")
                return render_template(
                    "cart_review.html", budgets=budgets, review=review
                )
            evidence[field] = {"path": path, "filename": stored_name}

        amount = cart_total
        notes = non_empty_str(request.form.get("notes", review.get("notes", "")))
        status = (
            services.STATUS_CONFIRMED if action == "purchase" else services.STATUS_DRAFT
        )
        try:
            transaction = services.create_transaction(
                budget_id=review["budget_id"],
                description=_short_cart_description(line_items),
                amount=amount,
                quantity=1,
                unit_price=None,
                receipt_path=evidence.get("receipt", {}).get("path"),
                receipt_filename=evidence.get("receipt", {}).get("filename"),
                additional_document_path=evidence.get("statement", {}).get("path"),
                additional_document_filename=evidence.get("statement", {}).get(
                    "filename"
                ),
                line_items=line_items,
                cart_summary={
                    **summary,
                    "ledger_amount": amount,
                    "ledger_amount_source": review.get("total_source"),
                },
                status=status,
                notes=notes,
            )
        except Exception:
            for saved in evidence.values():
                storage_delete(saved["path"])
            flash("장부 저장에 실패했습니다. DB 설정을 확인해 주세요.", "error")
            return render_template("cart_review.html", budgets=budgets, review=review)
        if not transaction:
            for saved in evidence.values():
                storage_delete(saved["path"])
            flash("장부 저장에 실패했습니다. 다시 시도해 주세요.", "error")
            return render_template("cart_review.html", budgets=budgets, review=review)
        session.pop("cart_image_review", None)
        flash(
            "구매 완료로 장부에 확정 등록했습니다."
            if action == "purchase"
            else "품의 완료로 장부에 임시 등록했습니다.",
            "success",
        )
        return redirect(url_for("app.transaction_detail", tx_id=transaction["id"]))

    return render_template(
        "cart_review.html", budgets=budgets, review=session.get("cart_image_review")
    )


@blueprint.route("/cart-proposal")
@check_access
def cart_proposal_list() -> str:
    """Saved proposals list (from DB)."""
    proposals = services.list_cart_proposals()
    return render_template("cart_proposal_list.html", proposals=proposals)


@blueprint.route("/cart-proposal/<proposal_id>")
@check_access
def cart_proposal_view(proposal_id: str) -> str:
    """단일 품의 초안 보기 + 코드 복사."""
    proposal = services.get_cart_proposal(proposal_id)
    if not proposal:
        abort(404)
    return render_template("cart_proposal_view.html", proposal=proposal)


@blueprint.route("/manual-entry", methods=["GET", "POST"])
@check_access
def manual_entry() -> Any:
    """수기 입력 페이지 (영수증 없이 거래 등록)."""
    budgets = services.list_budgets()
    if request.method == "POST":
        budget_id = non_empty_str(request.form.get("budget_id"))
        description = non_empty_str(request.form.get("description"))
        amount = safe_float(request.form.get("amount"))
        category = non_empty_str(request.form.get("category"))
        quantity = safe_int(request.form.get("quantity", 1))
        unit_price = safe_float(request.form.get("unit_price"))
        notes = non_empty_str(request.form.get("notes"))

        if not budget_id:
            flash("예산을 선택해 주세요.", "error")
            return redirect(url_for("app.manual_entry"))
        if not description:
            flash("설명을 입력해 주세요.", "error")
            return redirect(url_for("app.manual_entry"))
        if not is_valid_amount(amount):
            flash("금액은 0 이상의 숫자를 입력해 주세요.", "error")
            return redirect(url_for("app.manual_entry"))

        tx = services.create_transaction(
            budget_id=budget_id,
            description=description,
            amount=amount,
            category=category,
            quantity=quantity,
            unit_price=unit_price if unit_price else None,
            notes=notes,
        )
        if tx:
            flash("거래가 등록되었습니다.", "success")
            return redirect(url_for("app.ledger"))
        flash("등록 실패했습니다.", "error")
    return render_template("manual_entry.html", budgets=budgets)


# --------------
# API (JSON)
# --------------


@blueprint.route("/api/balance")
@check_access
def api_balance() -> Response:
    """예산별 잔액 API."""
    budgets = services.list_budgets()
    result = []
    for b in budgets:
        balance = services.get_budget_balance(b["id"])
        result.append(
            {
                "id": b["id"],
                "name": b["name"],
                "amount": float(b["amount"]),
                "balance": balance if balance is not None else 0,
            }
        )
    return Response(
        response=json.dumps(result), status=200, mimetype="application/json"
    )


@blueprint.route("/api/ledger")
@check_access
def api_ledger() -> Response:
    """거래 목록 API."""
    budget_id = request.args.get("budget_id", "")
    status = request.args.get("status", "")
    transactions = services.list_transactions(
        budget_id=budget_id if budget_id else None,
        status=status if status else None,
    )
    out = []
    for tx in transactions:
        out.append(
            {
                "id": tx["id"],
                "budget_id": tx["budget_id"],
                "description": tx["description"],
                "amount": float(tx["amount"]),
                "category": tx.get("category", ""),
                "quantity": tx.get("quantity", 1),
                "unit_price": float(tx["unit_price"])
                if tx.get("unit_price") is not None
                else None,
                "status": tx.get("status", ""),
                "receipt_path": tx.get("receipt_path"),
                "receipt_filename": tx.get("receipt_filename"),
                "notes": tx.get("notes", ""),
                "refund_amount": float(tx["refund_amount"])
                if tx.get("refund_amount") is not None
                else None,
                "refund_reason": tx.get("refund_reason", ""),
                "created_at": tx.get("created_at"),
                "updated_at": tx.get("updated_at"),
            }
        )
    return Response(response=json.dumps(out), status=200, mimetype="application/json")


@blueprint.route("/logout")
def logout_route() -> Response:
    return logout()
