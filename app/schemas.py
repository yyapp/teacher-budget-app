# -*- coding: utf-8 -*-
"""
teacher-budget-app / app/schemas.py

인식 초안, 수기 초안, 확정 저장용 입력 구조를 정리한다.
이 파일의 구조는 장부가 아니라 초안/임시/교환 자료에 가깝다.
"""

from typing import Any, Dict, List, Optional

DocumentType = str
Status = str

RECEIPT_DOC_TYPES = ["receipt", "cart", "budget", "form_requirements", "other"]


def draft_meta(document_type: str, source_file_name: str, processing_status: str, review_notes: List[str]) -> Dict[str, Any]:
    return {
        "document_type": document_type,
        "source_file_name": source_file_name,
        "processing_status": processing_status,
        "review_notes": review_notes,
    }


def receipt_draft(
    source_file_name: str,
    merchant_name: str = "",
    total_amount: Optional[float] = None,
    date: str = "",
    date_basis: str = "문서 명시",
    budget_name: str = "",
    approval_number: str = "",
    payment_method: str = "",
    discount_amount: Optional[float] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    review_notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "document_type": "receipt",
        "source_file_name": source_file_name,
        "processing_status": "임시",
        "budget_name": budget_name,
        "budget_selection_required": bool(budget_name == ""),
        "date": date,
        "date_basis": date_basis,
        "merchant_name": merchant_name,
        "approval_number": approval_number,
        "payment_method": payment_method,
        "total_amount": total_amount,
        "discount_amount": discount_amount,
        "items": items or [],
        "review_notes": review_notes or [],
    }


def cart_draft(
    source_file_name: str,
    merchant_name: str = "",
    expected_total_amount: Optional[float] = None,
    shipping_fee: Optional[float] = None,
    discount_terms: str = "",
    document_date: str = "",
    budget_name: str = "",
    items: Optional[List[Dict[str, Any]]] = None,
    review_notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "document_type": "cart",
        "source_file_name": source_file_name,
        "processing_status": "임시",
        "merchant_name": merchant_name,
        "expected_total_amount": expected_total_amount,
        "shipping_fee": shipping_fee,
        "discount_terms": discount_terms,
        "document_date": document_date,
        "budget_name": budget_name,
        "note": "구매 예정 참고자료 · 결제 증빙 아님",
        "items": items or [],
        "review_notes": review_notes or [],
    }


def item_draft(
    item_name: str = "",
    specification: str = "",
    quantity: float = 0,
    unit: str = "",
    unit_price: float = 0,
    item_amount: float = 0,
    selection_status: str = "선택",
) -> Dict[str, Any]:
    return {
        "item_name": item_name,
        "specification": specification,
        "quantity": quantity,
        "unit": unit,
        "unit_price": unit_price,
        "item_amount": item_amount,
        "selection_status": selection_status,
    }
