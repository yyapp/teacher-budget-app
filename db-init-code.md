# SQLite 테이블 생성 코드

작성일: 2026-11-05  
대상: teacher-budget-app / app/database.py  
목적: 로컬 SQLite 장부를 처음 실행할 때 테이블이 준비되도록 함

---

## 1. 설계 원칙

- 행 번호, 파일명을 영구 키로 쓰지 않는다.
- ID는 텍스트 UUID 형태로 생성한다.
- 숫자는 숫자로 저장하고 빈 값과 0을 구분한다.
- 외래키는 관계에 맞게 두되, 생성/삭제 순서를 고려한다.

---

## 2. 테이블 생성 순서

1. budget
2. transaction
3. item
4. budget_allocation
5. refund
6. receipt_file
7. request_log

이유:
- transaction이 budget_id를 참조하므로 budget을 먼저 만든다.
- item, budget_allocation, refund, receipt_file이 transaction_id를 참조하므로 transaction 이후에 만든다.

---

## 3. DDL 예시

```sql
-- 실행 전: 필요 시 외래키 활성화
PRAGMA foreign_keys = ON;

-- 1) budget
CREATE TABLE IF NOT EXISTS budget (
    budget_id TEXT NOT NULL PRIMARY KEY,
    budget_name TEXT NOT NULL,
    year INTEGER NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('확정','계획')),
    allocated_amount REAL NOT NULL,
    applied_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 2) transaction
CREATE TABLE IF NOT EXISTS transaction (
    transaction_id TEXT NOT NULL PRIMARY KEY,
    budget_id TEXT NOT NULL,
    merchant_name TEXT NOT NULL,
    transaction_date TEXT NOT NULL,
    total_amount REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('확정','임시','취소')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (budget_id) REFERENCES budget(budget_id)
);

-- 3) item
CREATE TABLE IF NOT EXISTS item (
    item_id TEXT NOT NULL PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    specification TEXT,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    unit_price REAL NOT NULL,
    item_amount REAL NOT NULL,
    selection_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (transaction_id) REFERENCES transaction(transaction_id)
);

-- 4) budget_allocation
CREATE TABLE IF NOT EXISTS budget_allocation (
    allocation_id TEXT NOT NULL PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    budget_id TEXT NOT NULL,
    allocated_amount REAL NOT NULL,
    unallocated_amount REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (transaction_id) REFERENCES transaction(transaction_id),
    FOREIGN KEY (budget_id) REFERENCES budget(budget_id)
);

-- 5) refund
CREATE TABLE IF NOT EXISTS refund (
    refund_id TEXT NOT NULL PRIMARY KEY,
    original_transaction_id TEXT NOT NULL,
    refund_date TEXT NOT NULL,
    refund_amount REAL NOT NULL,
    budget_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (original_transaction_id) REFERENCES transaction(transaction_id),
    FOREIGN KEY (budget_id) REFERENCES budget(budget_id)
);

-- 6) receipt_file
CREATE TABLE IF NOT EXISTS receipt_file (
    receipt_id TEXT NOT NULL PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    file_type TEXT NOT NULL CHECK(file_type IN ('원본','정리본','json')),
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    original_total REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (transaction_id) REFERENCES transaction(transaction_id)
);

-- 7) request_log
CREATE TABLE IF NOT EXISTS request_log (
    request_id TEXT NOT NULL PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    related_transaction_id TEXT,
    related_receipt_id TEXT,
    error_note TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (related_transaction_id) REFERENCES transaction(transaction_id),
    FOREIGN KEY (related_receipt_id) REFERENCES receipt_file(receipt_id)
);
```

---

## 4. 사용 참고

- `applied_date`, `transaction_date`, `refund_date`는 텍스트로 저장하되, 앱에서 비교/정렬할 때 일관된 형식을 사용한다.
- `allocated_amount`, `total_amount`, `item_amount`, `unit_price`, `allocated_amount`, `unallocated_amount`, `refund_amount`는 실수 범위를 허용하되, 통화 단위 표현 정책은 앱에서 통일한다.
- CHECK 제약으로 상태 값을 제한해 두었지만, 앱에서 입력 단계에도 같은 규칙을 적용한다.
- 처음에는 인덱스를 굳이 많이 만들지 않아도 된다. 다만 budget_name 검색이나 거래일자 정렬이 자주 쓰이면 나중에 추가한다.

---

## 5. 아직 미정

- UUID 생성 방식을 앱 어디의 어떤 함수로 할지
- 날짜/시간 문자 형식 기준을 앱 전체로 어디에 규정할지
- 통화 소수점/표시 정책
- 테이블별 공통 created_at/updated_at 기록 방식
