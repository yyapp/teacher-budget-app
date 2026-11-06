# 인식 초안 JSON 스키마

작성일: 2026-11-05  
대상: teacher-budget-app / app/schemas.py 및 초안 처리 흐름  
목적: 이미지 인식 결과나 수기 초안을 임시로 받을 때 쓰는 공통 구조 정리  
역할: 이 파일은 장부가 아니라 초안/임시/교환 자료다

---

## 1. 원칙

- 인식 초안은 아직 확정 전이다. 확정 전에는 SQLite 장부와 분리한다.
- 예시값이나 추정값을 실제 확정값으로 쓰지 않는다.
- 날짜가 미확인이면 오늘로 대체하지 않는다.
- 문서 유형이 달라도 공통 메타 필드는 공유한다.

---

## 2. 공통 메타 필드

모든 초안에 아래 필드가 있다.

```json
{
  "document_type": "receipt" | "cart" | "budget" | "form_requirements" | "other",
  "source_file_name": "원본 파일명",
  "processing_status": "임시" | "확인중" | "확정준비" | "실패",
  "review_notes": ["확인사항", "경고", "미확인 항목"]
}
```

설명:
- `document_type`은 문서 분류 결과다.
- `processing_status`는 초안 상태를 나타낸다.
- `review_notes`는 확인사항, 경고, 미확인 항목을 남긴다.
- `other`는 분류만 하고 추출하지 않는다.

---

## 3. 영수증 초안(receipt)

```json
{
  "document_type": "receipt",
  "source_file_name": "2026-11-05_영수증.png",
  "processing_status": "임시",
  "budget_name": "학급운영비",
  "budget_selection_required": true,
  "date": "2026-11-05",
  "date_basis": "문서 명시",
  "merchant_name": "문구점",
  "approval_number": "12345678",
  "payment_method": "카드",
  "total_amount": 24000,
  "discount_amount": 0,
  "items": [
    {
      "item_name": "크레파스 세트",
      "specification": "36색",
      "quantity": 2,
      "unit": "개",
      "unit_price": 12000,
      "item_amount": 24000,
      "selection_status": "선택"
    }
  ],
  "review_notes": ["총액과 품목 합계 일치 여부 확인 필요"]
}
```

필드 설명:
- `budget_name`: 문서에 적힌 예산명. 없으면 비우거나 null로 둔다.
- `budget_selection_required`: 예산 선택이 필요한지 여부.
- `date`: 문서상 날짜.
- `date_basis`: 날짜 근거. 예: `문서 명시`, `추정 불가`.
- `merchant_name`: 사용처.
- `approval_number`: 승인번호 등.
- `payment_method`: 결제수단.
- `total_amount`: 총액.
- `discount_amount`: 할인액.
- `items`: 품목 목록.
- `review_notes`: 확인사항.

주의:
- 거스름돈, 받은 금액, 할인 전 합계를 실제 결제액으로 잘못 추출하지 않는다.
- 총액과 품목 합계가 다르면 확인사항으로 표시한다.

---

## 4. 장바구니 초안(cart)

```json
{
  "document_type": "cart",
  "source_file_name": "2026-11-05_장바구니.png",
  "processing_status": "임시",
  "merchant_name": "온라인몰",
  "expected_total_amount": 100000,
  "shipping_fee": 3000,
  "discount_terms": "회원가 할인 적용 가능",
  "document_date": "2026-11-05",
  "budget_name": "",
  "note": "구매 예정 참고자료 · 결제 증빙 아님",
  "items": [
    {
      "item_name": "사무용 파일박스",
      "specification": "A4",
      "quantity": 2,
      "unit": "개",
      "unit_price": 15000,
      "item_amount": 30000,
      "selection_status": "선택"
    }
  ],
  "review_notes": ["실제 결제 여부 미확인"]
}
```

필드 설명:
- `expected_total_amount`: 예상 총액.
- `shipping_fee`: 배송비.
- `discount_terms`: 할인 조건.
- `document_date`: 문서 날짜.
- `note`: 장바구니는 결제 증빙이 아님을 표시한다.
- `items`: 품목 목록.

주의:
- 장바구니 예정액은 실제 지출로 합산하지 않는다.
- 추천·광고 상품은 제외하고 선택 여부를 확인한다.
- 선택 불명확 품목은 별도 표시한다.
- 이미 반영된 할인을 다시 빼지 않는다.

---

## 5. 예산 현황 초안(budget)

```json
{
  "document_type": "budget",
  "source_file_name": "2026-예산현황.hwpx",
  "processing_status": "임시",
  "budget_name": "선도교원",
  "period": "2026-03-01 ~ 2026-12-31",
  "allocated_amount": 300000,
  "recorded_spent_amount": 0,
  "recorded_balance": 300000,
  "transaction_list": [
    {
      "date": "2026-11-05",
      "merchant_name": "",
      "amount": 0,
      "budget_name": "",
      "note": ""
    }
  ],
  "budget_item_list": [
    {
      "item_name": "연수 재료",
      "item_amount": 0
    }
  ],
  "review_notes": ["실제 배정액과 문서 기재 금액 분리 필요"]
}
```

필드 설명:
- `period`: 기간.
- `allocated_amount`: 배정액.
- `recorded_spent_amount`: 기재된 순지출.
- `recorded_balance`: 기재된 잔액.
- `transaction_list`: 거래 내역.
- `budget_item_list`: 예산 항목 목록.

주의:
- 예시값은 실제 데이터로 쓰지 않는다.
- 실제 지원 여부와 완료 상태는 정확히 보고한다.
- 연결·실행·다운로드를 하지 않았는데 완료했다고 말하지 않는다.

---

## 6. 양식 요구사항 초안(form_requirements)

```json
{
  "document_type": "form_requirements",
  "source_file_name": "품의서양식.xlsx",
  "processing_status": "임시",
  "title": "품의서",
  "purpose": "구매 품의",
  "input_fields": ["부서", "작성자", "예산명", "품목", "예상 금액"],
  "repeat_table_columns": ["품목", "규격", "수량", "단가", "금액"],
  "submission_conditions": ["결재선 통과 후 사용"],
  "example_values_are_not_real_data": true,
  "review_notes": ["입력 칸과 반복 표 열 위치 확인 필요"]
}
```

필드 설명:
- `title`: 제목.
- `purpose`: 용도.
- `input_fields`: 입력 칸.
- `repeat_table_columns`: 반복 표 열.
- `submission_conditions`: 제출 조건.
- `example_values_are_not_real_data`: 예시값 배제 표시.

주의:
- 양식 예시값은 실제 지출로 취급하지 않는다.
- 담당자·서명·도장·승인일은 추측하지 않는다.
- 없는 값은 직접 입력하거나 빈칸으로 둔다.

---

## 7. 저장 형식으로 변환 시 참고

초안은 확인 화면을 통과한 뒤 SQLite 저장 형식으로 변환한다.

- `document_type`, `budget_name`은 budget 탐색/선택에 쓴다.
- `merchant_name`, `date`, `total_amount`는 transaction에 쓴다.
- `items`는 item에 쓴다.
- 예산 배분이 있으면 budget_allocation으로 나눈다.
- 파일 연결 정보는 receipt_file로 저장한다.

변환 규칙:
- 초안의 예시값, 추정값, 빈 값은 자동으로 채우지 않는다.
- 미확인 금액은 0으로 대체하지 않는다.
- date가 미확인이면 임시 상태로 유지한다.
- budget_selection_required가 true면 budget 선택할 때까지 확정하지 않는다.

---

## 8. 아직 미정

- 초안 상태 전이 규칙의 세부 단계
- 실패/부분성공 초안의 재시도 표현 방식
- 수령 파일명, 원본/정리본/JSON 연결 정보의 초안 포함 여부
- 외부 인식 서비스 응답을 이 스키마로 맞추는 변환 규칙
- 다국어/한글 처리 정책 상세
