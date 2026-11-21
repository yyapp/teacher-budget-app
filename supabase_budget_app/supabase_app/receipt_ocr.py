"""Upstage OCR client and receipt-field extraction."""

import os
import re
from datetime import date
from typing import Any, Optional

import httpx


class ReceiptOcrError(RuntimeError):
    """Raised when OCR cannot read a receipt."""


AMOUNT_RE = re.compile(r"(?<!\d)(\d[\d,]*)(?:\s*원)?")
CURRENCY_RE = re.compile(r"(?<!\d)(\d[\d,]*)\s*원")
TOTAL_LABELS = (
    re.compile(r"실\s*결제\s*금액|최종\s*결제\s*금액|카드\s*결제\s*금액"),
    re.compile(r"승인\s*금액|결제\s*금액|받을\s*금액|청구\s*금액|결제액"),
    re.compile(r"총\s*주문\s*금액|총\s*결제\s*금액|총\s*결제|총\s*액|합\s*계"),
)
IGNORE_RE = re.compile(
    r"사업자|전화|주소|카드번호|승인번호|부가세|공급가|과세|면세|거스름|받은\s*금액|포인트|할인|취소|잔액|배송|배달|택배|송장|운송장|발송|수령|받는\s*분|보내는\s*분|우편번호|주문번호|주문일|배송완료|배송지|도로명|번길|특별시|광역시|자치시|서울\s|부산\s|대구\s|인천\s|광주\s|대전\s|울산\s|세종\s|경기도\s|강원도\s|충청|전라|경상|제주도\s"
)
NON_ITEM_RE = re.compile(
    r"사업자|등록번호|대표자|전화|연락처|주소|우편번호|도로명|번길|특별시|광역시|자치시|서울\s|부산\s|대구\s|인천\s|광주\s|대전\s|울산\s|세종\s|경기도\s|강원도\s|충청|전라|경상|제주도\s|배송|배달|택배|송장|운송장|발송|수령|받는\s*분|보내는\s*분|주문|결제|카드|승인|영수증|합\s*계|총\s*액|할인|쿠폰|적립|포인트|부가세|공급가|과세|면세|거스름|잔액|감사합니다"
)
ADDRESS_COMPONENT_RE = re.compile(
    r"(?:^|\s)[가-힣]{1,8}(?:구|군|시|읍|면|동|리|로|길)\s+"
    r"[가-힣0-9-]*(?:로|길|동|읍|면|리|호|\d)(?:\s|$)"
)
QTY_RE = re.compile(r"(\d+)\s*(?:개입|개|권|병|ea|EA|세트|팩)")
DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})(?:[./-](\d{1,2})[./-](\d{1,2})|(\d{2})(\d{2}))(?!\d)"
)
VENDOR_SKIP_RE = re.compile(
    r"영수증|거래명세|견적서|사업자|등록번호|전화|주소|카드|승인|결제|합계|총액|부가세|공급가|주문|내역|상세|상품|배송"
)
CART_UI_NOISE_RE = re.compile(
    r"장바구니|바로\s*구매|옵션\s*저장|옵션\s*변경|총\s*상품\s*금액|상품\s*합계|상품\s*금액|배송비|배송비\s*무료|결제\s*예정|주문\s*금액|결제\s*금액|쿠폰|할인|적립금|적립|선택상품|전체선택|삭제|수량변경|품절|구매하기|찜하기|마이페이지|장바구니에\s*담기|배송완료|배송지|주문번호|주문일"
)
CART_PRODUCT_TOTAL_RE = re.compile(r"상품\s*(?:합계|금액)|총\s*상품\s*금액|상품금액")
CART_SHIPPING_RE = re.compile(r"배송비|배송료|택배비")
CART_DISCOUNT_RE = re.compile(r"할인|쿠폰|차감|사용\s*포인트")
CART_PAYABLE_RE = re.compile(
    r"결제\s*예정\s*금액|최종\s*결제\s*금액|주문\s*금액|결제\s*예정|결제금액"
)


def _amounts(line: str) -> list[int]:
    cleaned = DATE_RE.sub(" ", line)
    cleaned = re.sub(
        r"(?:승인번호|주문번호|카드번호|사업자등록번호|전화번호|연락처|우편번호|운송장번호|송장번호)\s*[:#]?\s*[\d-]+",
        " ",
        cleaned,
    )
    return [
        int(match.group(1).replace(",", "")) for match in AMOUNT_RE.finditer(cleaned)
    ]


def _currency_amounts(line: str) -> list[int]:
    return [
        int(match.group(1).replace(",", "")) for match in CURRENCY_RE.finditer(line)
    ]


def _price_candidates(line: str) -> list[int]:
    """Read prices with or without a currency suffix, excluding trailing quantities."""
    prices = _currency_amounts(line)
    if prices:
        return prices
    quantity_match = re.search(
        r"(?:\||/)?\s*\d+\s*(?:개|개입|권|병|ea|EA|세트|팩)\s*$",
        line,
        re.I,
    )
    if not quantity_match and re.search(
        r"\d\s*(?:g|kg|ml|l|cm|mm|개입|입)\b", line, re.I
    ):
        return []
    price_line = line[: quantity_match.start()] if quantity_match else line
    return [amount for amount in _amounts(price_line) if amount >= 100]


def _is_non_item_line(line: str) -> bool:
    return bool(NON_ITEM_RE.search(line) or ADDRESS_COMPONENT_RE.search(line))


def _choose_price_candidates(
    items: list[dict[str, Any]], target_total: Optional[float] = None
) -> None:
    """Only resolve prices whose unit-times-quantity relation proves the row total."""
    del target_total  # A document summary must never be used to force item prices.
    for item in items:
        quantity = max(int(item.get("quantity") or 1), 1)
        candidates = list(
            dict.fromkeys(
                int(value)
                for value in item.get("price_candidates", [])
                if int(value) > 0
            )
        )
        item["price_candidates"] = candidates
        if not candidates:
            item["amount"] = None
            item["unit_price"] = None
            item["needs_review"] = True
            continue
        if len(candidates) == 1:
            amount = candidates[0]
        else:
            related = {
                larger
                for unit in candidates
                for larger in candidates
                if unit != larger and unit * quantity == larger
            }
            if len(related) != 1:
                item["amount"] = None
                item["unit_price"] = None
                item["needs_review"] = True
                continue
            amount = next(iter(related))
        item["amount"] = amount
        item["unit_price"] = amount / quantity
        item["needs_review"] = False


def _ocr_text(payload: dict[str, Any]) -> str:
    """Rebuild OCR text into visual rows when word coordinates are available."""
    visual_lines = []
    for page in payload.get("pages", []):
        words = page.get("words", [])
        positioned = []
        for word in words:
            vertices = word.get("boundingBox", {}).get("vertices", [])
            if vertices:
                center_y = sum(point.get("y", 0) for point in vertices) / len(vertices)
                left_x = min(point.get("x", 0) for point in vertices)
                positioned.append((center_y, left_x, word.get("text", "")))
        if not positioned:
            continue
        positioned.sort()
        row_tolerance = page.get("height", 0) * 0.012
        if row_tolerance <= 0.03:
            row_tolerance = 0.012
        else:
            row_tolerance = max(row_tolerance, 3)
        rows: list[tuple[float, list[tuple[float, str]]]] = []
        for center_y, left_x, word_text in positioned:
            if rows and abs(center_y - rows[-1][0]) <= row_tolerance:
                rows[-1][1].append((left_x, word_text))
            else:
                rows.append((center_y, [(left_x, word_text)]))
        visual_lines.extend(
            " ".join(text for _, text in sorted(row)) for _, row in rows
        )
    if visual_lines:
        return "\n".join(visual_lines)
    page_texts = [page.get("text", "") for page in payload.get("pages", [])]
    page_text = "\n".join(text for text in page_texts if text)
    return page_text or payload.get("text", "")


def extract_receipt_fields(text: str) -> dict[str, Any]:
    """Extract receipt metadata and one structured row per detected item."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    total = None
    total_priority = len(TOTAL_LABELS)
    receipt_date = ""
    vendor = ""
    for index, line in enumerate(lines):
        if not receipt_date:
            date_match = DATE_RE.search(line)
            if date_match:
                year, month, day = (
                    map(int, date_match.groups()[:3])
                    if date_match.group(2)
                    else (
                        int(date_match.group(1)),
                        int(date_match.group(4)),
                        int(date_match.group(5)),
                    )
                )
                try:
                    receipt_date = date(year, month, day).isoformat()
                except ValueError:
                    pass
        if (
            not vendor
            and len(line) <= 48
            and not _amounts(line)
            and not VENDOR_SKIP_RE.search(line)
        ):
            if not re.search(r"\d{2,}", line) and re.search(r"[가-힣A-Za-z]", line):
                vendor = line.strip(" -:|")
        if _is_non_item_line(line):
            for priority, label_pattern in enumerate(TOTAL_LABELS):
                if not label_pattern.search(line):
                    continue
                numbers = _price_candidates(line)
                if not numbers and index + 1 < len(lines):
                    numbers = _price_candidates(lines[index + 1])
                if numbers and priority < total_priority:
                    total = numbers[0]
                    total_priority = priority
                break

    items: list[dict[str, Any]] = []
    pending_description = ""
    pending_is_vendor = False
    for line in lines:
        if _is_non_item_line(line) or IGNORE_RE.search(line):
            pending_description = ""
            pending_is_vendor = False
            continue
        if DATE_RE.search(line):
            continue
        prices = _price_candidates(line)
        if not prices:
            if re.search(r"[가-힣A-Za-z]", line) and len(line) <= 100:
                cleaned = re.sub(r"\s+", " ", line).strip(" -:|")
                if cleaned and not VENDOR_SKIP_RE.search(cleaned):
                    if vendor and cleaned == vendor:
                        pending_description = cleaned
                        pending_is_vendor = True
                    elif (
                        pending_is_vendor
                        or cleaned.startswith("[")
                        or QTY_RE.search(cleaned)
                        or not pending_description
                    ):
                        pending_description = cleaned
                        pending_is_vendor = False
                    else:
                        pending_description = f"{pending_description} {cleaned}"[:120]
            continue

        inline_description = QTY_RE.sub(" ", line)
        inline_description = AMOUNT_RE.sub(" ", inline_description)
        inline_description = re.sub(r"\s+", " ", inline_description).strip(" -:|/()")
        if inline_description and re.search(r"[가-힣A-Za-z]", inline_description):
            pending_description = inline_description
            pending_is_vendor = False
        if not pending_description:
            continue

        quantity_matches = list(QTY_RE.finditer(line))
        quantity = int(quantity_matches[-1].group(1)) if quantity_matches else 1
        # Order-detail images show the bold price as the total for this item row.
        line_amount = prices[0] if prices else None
        unit_price = line_amount / max(quantity, 1) if line_amount is not None else None
        description = re.sub(r"\s+", " ", pending_description).strip(" -:|/")
        items.append(
            {
                "description": description,
                "quantity": max(quantity, 1),
                "unit_price": unit_price,
                "amount": line_amount if line_amount is not None else "",
                "price_candidates": prices,
                "needs_review": not quantity_matches,
            }
        )
        pending_description = ""
        pending_is_vendor = False

    _choose_price_candidates(items, total)
    description = ", ".join(item["description"] for item in items)
    quantity = sum(item["quantity"] for item in items) if items else 1
    items_total = sum(
        item["amount"] for item in items if isinstance(item["amount"], (int, float))
    )
    unresolved_prices = any(
        not isinstance(item["amount"], (int, float)) for item in items
    )
    return {
        "description": description,
        "amount": total or "",
        "items_total": items_total,
        "total_difference": (
            total - items_total if total is not None and not unresolved_prices else None
        ),
        "quantity": quantity,
        "items": items,
        "receipt_date": receipt_date,
        "vendor": vendor,
        "raw_text": text,
    }


def cart_items_from_ocr(
    items: list[dict[str, Any]], final_total: Optional[float] = None
) -> list[dict[str, Any]]:
    """Preserve order quantity separately; each cart row is one ledger entry."""
    del final_total
    cart_items = []
    for item in items:
        order_quantity = max(
            int(item.get("order_quantity", item.get("quantity")) or 1), 1
        )
        amount = item.get("item_amount", item.get("amount"))
        unit_price = item.get("unit_price")
        cart_items.append(
            {
                "description": item["description"],
                "option": item.get("option", ""),
                "package_size": item.get("package_size", ""),
                "quantity": 1,
                "order_quantity": order_quantity,
                "amount_candidates": item.get("price_candidates", []),
                "amount": amount if isinstance(amount, (int, float)) else "",
                "unit_price": unit_price,
                "needs_review": bool(item.get("needs_review"))
                or not isinstance(amount, (int, float)),
                "source_text": item.get("source_text", ""),
            }
        )
    return cart_items


def _summary_money(line: str) -> Optional[int]:
    values = _price_candidates(line)
    return values[-1] if values else None


def _package_size(description: str) -> str:
    match = re.search(
        r"(?<!\w)\d+(?:\.\d+)?\s*(?:kg|g|ml|l|개입|입|팩|세트)(?=\s|$|[),])",
        description,
        re.I,
    )
    return match.group(0) if match else ""


def extract_cart_fields(text: str) -> dict[str, Any]:
    """Extract product rows and order totals independently from shopping-cart OCR."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    summary: dict[str, Optional[int]] = {
        "products_subtotal": None,
        "shipping": None,
        "discount": None,
        "payable": None,
    }
    items: list[dict[str, Any]] = []
    pending_name = ""
    pending_option = ""
    summary_seen = False
    discount_seen = False

    for line in lines:
        if CART_DISCOUNT_RE.search(line):
            discount_seen = True
        is_summary = bool(
            CART_PRODUCT_TOTAL_RE.search(line)
            or CART_SHIPPING_RE.search(line)
            or CART_DISCOUNT_RE.search(line)
            or CART_PAYABLE_RE.search(line)
        )
        if is_summary:
            summary_seen = True
            amount = _summary_money(line)
            if (
                CART_SHIPPING_RE.search(line)
                and amount is None
                and re.search(r"무료|0\s*원", line)
            ):
                amount = 0
            if (
                CART_DISCOUNT_RE.search(line)
                and amount is None
                and re.search(r"없음|적용\s*안함", line)
            ):
                amount = 0
            if amount is not None:
                if CART_PRODUCT_TOTAL_RE.search(line):
                    summary["products_subtotal"] = amount
                elif CART_SHIPPING_RE.search(line):
                    summary["shipping"] = (summary["shipping"] or 0) + amount
                elif CART_DISCOUNT_RE.search(line):
                    discount_seen = True
                    summary["discount"] = (summary["discount"] or 0) + amount
                elif CART_PAYABLE_RE.search(line):
                    summary["payable"] = amount
            pending_name = ""
            pending_option = ""
            continue
        if CART_UI_NOISE_RE.search(line) or _is_non_item_line(line):
            pending_name = ""
            pending_option = ""
            continue

        prices = _price_candidates(line)
        if not prices:
            if re.search(r"[가-힣A-Za-z]", line) and not DATE_RE.search(line):
                cleaned = re.sub(r"\s+", " ", line).strip(" -:|")
                # Quantity/option details may wrap onto the preceding product title.
                if cleaned and len(cleaned) <= 160:
                    if pending_name and cleaned.startswith(("옵션", "색상", "사이즈")):
                        pending_option = cleaned
                    elif pending_name and not cleaned.startswith(("[",)):
                        pending_name = f"{pending_name} {cleaned}"[:200]
                    else:
                        pending_name = cleaned
            continue

        quantity_matches = list(QTY_RE.finditer(line))
        order_quantity = int(quantity_matches[-1].group(1)) if quantity_matches else 1
        inline_name = AMOUNT_RE.sub(" ", QTY_RE.sub(" ", line))
        inline_name = re.sub(r"\s+", " ", inline_name).strip(" -:|/()")
        if inline_name and re.search(r"[가-힣A-Za-z]", inline_name):
            pending_name = inline_name
        if not pending_name:
            continue

        unit_price = None
        item_amount = None
        candidate_prices = list(dict.fromkeys(prices))
        if len(candidate_prices) == 1:
            item_amount = candidate_prices[0]
            unit_price = item_amount / order_quantity
        else:
            arithmetic_pairs = [
                (unit, total)
                for unit in candidate_prices
                for total in candidate_prices
                if unit != total and unit * order_quantity == total
            ]
            if len(set(arithmetic_pairs)) == 1:
                unit_price, item_amount = arithmetic_pairs[0]

        items.append(
            {
                "description": pending_name,
                "option": pending_option,
                "package_size": _package_size(pending_name),
                "order_quantity": order_quantity,
                "quantity": 1,
                "unit_price": unit_price,
                "item_amount": item_amount,
                "amount": item_amount,
                "price_candidates": candidate_prices,
                "needs_review": item_amount is None,
                "source_text": line,
            }
        )
        pending_name = ""
        pending_option = ""

    discount_inferred_zero = False
    if (
        summary_seen
        and not discount_seen
        and summary["products_subtotal"] is not None
        and summary["shipping"] is not None
        and summary["payable"] is not None
    ):
        # This order summary exposes all payable components but has no discount row.
        summary["discount"] = 0
        discount_seen = True
        discount_inferred_zero = True

    known_item_amounts = [
        item["item_amount"]
        for item in items
        if isinstance(item["item_amount"], (int, float))
    ]
    calculated_items_subtotal = (
        sum(known_item_amounts)
        if len(known_item_amounts) == len(items) and items
        else None
    )
    products_status = (
        "unavailable"
        if summary["products_subtotal"] is None
        else "matched"
        if calculated_items_subtotal == summary["products_subtotal"]
        else "mismatch"
        if calculated_items_subtotal is not None
        else "review_required"
    )
    payable_components = (
        summary["products_subtotal"],
        summary["shipping"],
        summary["discount"] if discount_seen else None,
    )
    calculated_payable = None
    if all(value is not None for value in payable_components):
        product_subtotal = int(summary["products_subtotal"] or 0)
        shipping = int(summary["shipping"] or 0)
        discount = int(summary["discount"] or 0)
        calculated_payable = product_subtotal + shipping - discount
    payable_status = (
        "unavailable"
        if summary["payable"] is None
        else "matched"
        if calculated_payable == summary["payable"]
        else "mismatch"
        if calculated_payable is not None
        else "review_required"
    )
    return {
        "items": items,
        "products_subtotal": summary["products_subtotal"],
        "calculated_items_subtotal": calculated_items_subtotal,
        "shipping": summary["shipping"],
        "discount": summary["discount"],
        "discount_seen": discount_seen,
        "discount_inferred_zero": discount_inferred_zero,
        "payable": summary["payable"],
        "calculated_payable": calculated_payable,
        "products_status": products_status,
        "payable_status": payable_status,
        "summary_seen": summary_seen,
        "raw_text": text,
    }


def _request_document_text(file_bytes: bytes, filename: str, content_type: str) -> str:
    api_key = os.environ.get("UPSTAGE_API_KEY", "").strip()
    if not api_key:
        raise ReceiptOcrError("문서 자동 인식 설정이 없습니다. 관리자에게 알려 주세요.")

    try:
        response = httpx.post(
            "https://api.upstage.ai/v1/document-digitization",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"document": (filename, file_bytes, content_type)},
            data={"model": "ocr"},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ReceiptOcrError(
            "파일을 읽지 못했습니다. 선명한 이미지나 PDF로 다시 시도해 주세요."
        ) from exc

    text = _ocr_text(payload)
    if not text.strip():
        raise ReceiptOcrError(
            "파일에서 글자를 찾지 못했습니다. 더 선명한 이미지나 PDF로 다시 시도해 주세요."
        )
    return text


def parse_receipt(
    file_bytes: bytes, filename: str, content_type: str
) -> dict[str, Any]:
    """Send a receipt to OCR and return receipt-specific fields."""
    return extract_receipt_fields(
        _request_document_text(file_bytes, filename, content_type)
    )


def parse_cart_image(
    file_bytes: bytes, filename: str, content_type: str
) -> dict[str, Any]:
    """Send a pre-purchase cart image to OCR and extract cart-only fields."""
    return extract_cart_fields(
        _request_document_text(file_bytes, filename, content_type)
    )
