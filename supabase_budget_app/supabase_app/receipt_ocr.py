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
    """Choose OCR price candidates automatically, matching the known total when possible."""
    candidates_by_item = [
        list(
            dict.fromkeys(
                int(value)
                for value in item.get("price_candidates", [])
                if int(value) > 0
            )
        )
        for item in items
    ]
    if not items or not all(candidates_by_item):
        return

    if target_total and target_total > 0:
        choices: dict[int, tuple[int, ...]] = {0: ()}
        for candidates in candidates_by_item:
            next_choices: dict[int, tuple[int, ...]] = {}
            for subtotal, selection in choices.items():
                for index, amount in enumerate(candidates):
                    total = subtotal + amount
                    rank = selection + (index,)
                    if total not in next_choices or rank < next_choices[total]:
                        next_choices[total] = rank
            if len(next_choices) > 50000:
                nearest = sorted(
                    next_choices, key=lambda value: abs(value - target_total)
                )[:25000]
                next_choices = {value: next_choices[value] for value in nearest}
            choices = next_choices
        selected = choices[
            min(
                choices,
                key=lambda value: (abs(value - target_total), value > target_total),
            )
        ]
    else:
        selected = tuple(0 for _ in items)

    for item, candidates, index in zip(items, candidates_by_item, selected):
        amount = candidates[index]
        quantity = max(int(item.get("quantity") or 1), 1)
        item["amount"] = amount
        item["unit_price"] = amount / quantity
        item["selected_amount"] = amount
        item["needs_review"] = False


def _ocr_text(payload: dict[str, Any]) -> str:
    """Rebuild OCR text into visual rows when word coordinates are available."""
    page_texts = [page.get("text", "") for page in payload.get("pages", [])]
    page_text = "\n".join(text for text in page_texts if text)
    if page_text:
        return page_text
    if payload.get("text"):
        return payload["text"]

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
    return "\n".join(visual_lines)


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
    """Keep order quantity in the description and store each row as one ledger unit."""
    cart_items = []
    candidate_rows = []
    for item in items:
        quantity = max(int(item.get("quantity") or 1), 1)
        candidates = item.get("price_candidates") or []
        if not candidates and isinstance(item.get("amount"), (int, float)):
            candidates = [item["amount"]]
        candidates = list(
            dict.fromkeys(int(value) for value in candidates if int(value) > 0)
        )
        candidate_rows.append(candidates)
        cart_items.append(
            {
                "description": f"{item['description']} (주문 {quantity}개)",
                "quantity": 1,
                "amount_candidates": candidates,
                "amount": candidates[0] if candidates else "",
                "unit_price": candidates[0] if candidates else None,
                "needs_review": not candidates,
            }
        )

    if final_total and final_total > 0 and all(candidate_rows):
        choices: dict[int, tuple[int, ...]] = {0: ()}
        for candidates in candidate_rows:
            next_choices = {}
            for subtotal, selected in choices.items():
                for index, amount in enumerate(candidates):
                    total = subtotal + amount
                    rank = selected + (index,)
                    if total not in next_choices or rank < next_choices[total]:
                        next_choices[total] = rank
            if len(next_choices) > 50000:
                nearest = sorted(
                    next_choices, key=lambda value: abs(value - final_total)
                )[:25000]
                next_choices = {value: next_choices[value] for value in nearest}
            choices = next_choices
        selected_total = min(
            choices, key=lambda value: (abs(value - final_total), value > final_total)
        )
        selected_indexes = choices[selected_total]
        for item, candidates, index in zip(
            cart_items, candidate_rows, selected_indexes
        ):
            item["amount"] = candidates[index]
            item["unit_price"] = candidates[index]

    return cart_items


def parse_receipt(
    file_bytes: bytes, filename: str, content_type: str
) -> dict[str, Any]:
    """Send a receipt to Upstage OCR and return extracted fields."""
    api_key = os.environ.get("UPSTAGE_API_KEY", "").strip()
    if not api_key:
        raise ReceiptOcrError(
            "영수증 자동 인식 설정이 없습니다. 관리자에게 알려 주세요."
        )

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
            "영수증을 읽지 못했습니다. 선명한 이미지나 PDF로 다시 시도해 주세요."
        ) from exc

    text = _ocr_text(payload)
    if not text.strip():
        raise ReceiptOcrError(
            "영수증에서 글자를 찾지 못했습니다. 더 선명한 파일로 다시 시도해 주세요."
        )
    return extract_receipt_fields(text)
