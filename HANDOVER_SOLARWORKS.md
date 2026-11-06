# 인수인계 — 교사용 예산·구매 서류 도우미 (솔라웍스용)

작성일: 2026-11-06
용도: 솔라웍스에서 이어서 작업하고 **최종본을 완성**하기 위한 문서. 먼저 0~2장만 읽어도 작업을 시작할 수 있다.

---

## 0. 한눈에 보기

| 항목 | 내용 |
|---|---|
| 무엇 | 교사가 예산별 지출·영수증·장바구니(품의용)를 관리하는 **로컬 웹앱** |
| 스택 | Python 3.14 + Flask + SQLite + Jinja 템플릿 (외부 서비스 없음) |
| 작업 폴더 | 이 저장소(프로젝트 폴더) 전체 (**여기가 기준 본**) |
| 원본 폴더 | 개발 초기에 복제해 온 옛 원본 폴더(쓰기 제한 있음). 저장소에는 포함되지 않음. **건드리지 말 것** |
| 실행 | `cd project_work && source .venv/bin/activate && FLASK_PORT=5003 python app.py` |
| 접속 | `http://127.0.0.1:5003` (환경변수 없으면 5002). 템플릿 파일을 직접 열면 `{% %}`가 그대로 보이니 반드시 서버로 접속 |
| 자동 인식 키 | 프로젝트 폴더에 `.env` 파일을 만들고 `UPSTAGE_API_KEY=...` 한 줄. **저장소에는 올리지 말 것** (이미 `.gitignore` 처리됨). 없으면 자동 인식만 꺼지고 나머지는 정상 |
| 검증 | `python tests/e2e_smoke.py` → 마지막 줄이 `ALL PASS`여야 함 (임시 DB 사용, 실제 DB 안 건드림) |
| 사용자 | 비개발자 교사. 설명은 한국어, 쉽게 |

솔라웍스가 이 Mac 경로에 접근하지 못하면: `project_work/` 폴더 전체(`.venv`, `__pycache__`, `server.log`, `data/app.db` 제외)를 올리고, 작업 후 변경된 파일 목록과 내용을 돌려줄 것. 의존성은 `flask` 하나뿐이다.

---

## 1. 현재 상태 (2026-11-06 기준)

### 1.1 동작 확인됨 (`tests/e2e_smoke.py`로 검증)
- 전 화면 GET 200: `/`, `/budgets`, `/upload`, `/cart-review`, `/ledger`, `/manual-entry`, `/api/balance`, `/api/ledger`
- 예산 등록
- 영수증 업로드 POST (이미지 파일 포함/미포함) → 거래·품목·배정·증빙파일·요청이력 저장, 파일 실제 저장
- 장부에 거래 표시 (품목·증빙 포함)
- 장바구니 검토 → 품의 코드 복사 화면
- **거래 상태 변경 (임시/확정/취소)**: 장부 화면 버튼. 확정하면 잔액이 줄고, 취소하면 복구됨

### 1.2 이번 세션에서 고친 버그 (재발 방지용으로 기록)
| 증상 | 진짜 원인 | 수정 |
|---|---|---|
| 업로드 POST 500 `database is locked` | `save_transaction_with_items`가 **커밋하지 않은 쓰기 트랜잭션을 연 채** `log_request`가 **별도 DB 연결**로 쓰려다 자기 자신과 잠금 충돌. 함수에 `commit()`도 없었음. (이전 문서의 "CLI 동시 접근" 설명은 **틀림**) | `services.py`: 로그 전에 `commit()`, 실패 시 `rollback()`. `database.py`: `timeout=10` |
| 거래가 1건이라도 있으면 장부 화면이 깨짐 | Jinja에서 딕셔너리 `r.items`는 품목이 아니라 `dict.items()` 메서드로 해석됨 | 템플릿에서 `r["items"]`, `cart["items"]`, `draft["items"]` 사용 |
| 저장 파일명이 `…원본png` (점 없음) | `safe_filename(".png")`가 앞의 `.`을 지움 | `storage.py`: `"." + safe_filename(ext.lstrip("."))` |

**규칙**: 한 요청 안에서 SQLite 연결을 둘 이상 열고 쓰지 말 것. 쓰기 연결은 먼저 `commit()`한 뒤 다른 연결(예: `log_request`)을 쓴다.

### 1.3 추가된 기능 (이후 Claude 세션)
- 거래 상태 변경 (임시/확정/취소): `POST /transactions/<id>/status`
- **입력 검증** (`app/forms.py`): 숫자(쉼표 허용, 음수·NaN 거부), 품목 줄, 업로드 파일(png/jpg/jpeg/webp/**pdf**, 시그니처 확인, 10MB). 잘못된 입력은 500이 아니라 안내 문구로 표시
- **장바구니 DB 저장**: `cart_proposal` 테이블, `/cart-proposal?id=<번호>` (예전 `?cart=<JSON>` 주소도 호환)
- **환불**: `GET/POST /transactions/<id>/refund`, `POST /refunds/<id>/cancel`. 확정 거래만, 환불 합계는 사용액 이내, 잔액에 즉시 반영
- **거래 수정·삭제**: `/transactions/<id>/edit`, `POST /transactions/<id>/delete`. 삭제는 확정 거래·환불 내역 있는 거래를 막고, 증빙 파일도 함께 지움
- **영수증 자동 인식(OCR)**: `app/upstage_client.py`가 Upstage Information Extraction API(`/v1/information-extraction/chat/completions`, 모델 `information-extract`)를 호출. 이미지 또는 PDF를 올리면 거래처·날짜·총액·품목을 채워 `임시` 거래로 저장한 뒤 수정 화면으로 이동. 실패하면 이미지만 저장하고 안내. API 키는 프로젝트 폴더의 `.env`(`UPSTAGE_API_KEY=...`, Git 제외)에서 읽음. 이미지가 외부 서비스로 전송됨
- **업로드 단순화**: `/upload`는 예산 선택 + 파일(이미지/PDF)만 받음. 업로드하면 자동 인식 → 임시 거래로 저장 → `/transactions/<id>/edit?new=1`(인식 결과 확인 화면)로 이동. 이 화면에서 원본 미리보기(`/receipts/<id>/file`)와 비교하며 거래처·날짜·품목(수량·단가·금액)·총액을 고치고 **확정**(저장+확정) 또는 임시 저장. 수량·단가를 바꾸면 금액이 자동 계산되고, 품목 합계와 총액이 다르면 경고. 인식에 실패해도 파일은 저장되고 같은 화면에서 직접 입력
- **증빙 파일명 자동 규칙**: 업로드한 파일은 `예산명_일자_거래처.확장자`(예: `학급운영비_2026-11-05_행복문구점.png`)로 저장. 거래처·일자·예산명을 고치면 파일 이름도 자동으로 바뀜(`services.sync_receipt_file_names`), 같은 이름이 있으면 `_2`, `_3`. 일자가 없으면 `일자미확인`. 예전 이름의 파일은 증빙서류 화면을 열 때 정리됨
- **증빙서류 출력**: `/transactions/<id>/evidence` — 예산·일자·거래처·총액·품목 요약표 + 원본 이미지(PDF는 iframe, 인쇄 시 별도 첨부 안내)를 A4 한 장 인쇄 레이아웃으로 표시. 인쇄(또는 PDF로 저장), 원본 파일 저장(`/receipts/<id>/file?download=1`). 장부와 확인 화면에서 링크
- 예산 화면에 안내 문구(flash) 영역 추가 (`templates/_flash.html`)
- 검증: `python tests/e2e_smoke.py` (임시 DB, 현재 `ALL PASS`)

---

## 2. 범위

### 2.1 이번에 할 일 (완료 기준 포함)
`tests/e2e_smoke.py`가 `ALL PASS`인 상태를 **유지하면서**, 아래 중 사용자가 요청하는 것만 한다.

- [ ] 실제 서버를 띄워 브라우저에서 업로드 → 장부 → 확정 → 예산 현황(잔액) 흐름이 눈으로 확인되는지 점검
- [ ] 화면 사용성 다듬기 (교사가 헷갈리지 않게 문구·배치)
- [ ] 아래 3장의 "알려진 약점" 중 요청받은 것 수정

### 2.2 범위 밖 (하지 말 것)
- **구글 로그인, Google Drive/Sheets 동기화, 폰 연동, 공유 저장소** — 사용자가 취소함. 로컬 저장만 사용
- PDF/HWPX 변환, 학교 양식 출력(XLSX/PDF/HWPX) — 별도 과제
- 기존 테이블 구조 변경, 전체 재작성, 프레임워크 교체
- `Documents/project/`(원본) 수정

---

## 3. 남은 약점·과제

해결됨: 숫자·파일 입력 검증, 장바구니 URL 길이 문제, 환불 입력 화면, 거래 수정·삭제.

남은 것 (요청받을 때만):
1. **OCR 정확도는 영수증마다 다를 수 있음**: 인식 결과는 항상 `임시`로 저장되고 사람이 수정 화면에서 확인하도록 되어 있음. 실제 영수증 여러 장으로 정확도를 보고 스키마 설명(`RECEIPT_SCHEMA`)을 다듬을 여지가 있음. 장바구니 캡처 전용 처리는 아직 없음.
2. **입력 오류 시 폼 값이 사라짐**: 오류 후 리다이렉트하므로 사용자가 다시 입력해야 함. 폼을 다시 그리며 값을 유지하는 방식으로 개선 가능.
3. `_require_init()`이 예외를 삼킴 (`routes.py`): DB 초기화 실패가 조용히 묻힘.
4. `app.secret_key` 기본값이 코드에 하드코딩 (로컬 단일 사용자라 급하진 않음).
5. 문서 불일치: `db-init-code.md`는 테이블명 `transaction`, 실제 코드는 `transactions`.
6. 업로드·수기 입력은 항상 `임시`로 저장되므로 **장부에서 '확정'을 눌러야 잔액에 반영**됨 (안내 문구 보강 여지).
7. 실제 브라우저에서의 화면 확인은 사용자가 직접 해야 함 (테스트는 서버 응답·DB 기준).

---

## 4. 구조

```
project_work/
├── app.py                 # Flask 진입점 (FLASK_PORT, 기본 5002)
├── app/
│   ├── database.py        # SQLite 연결·스키마(SCHEMA_SQL). DB: data/app.db
│   ├── models.py          # 입력 검증 헬퍼
│   ├── routes.py          # 모든 라우트
│   ├── services.py        # 저장·잔액·장부·상태변경 로직
│   ├── status.py          # 요청 이력(request_log) 기록, 상태 상수
│   ├── storage.py         # 파일 저장 규칙 (storage/receipts/)
│   ├── schemas.py         # 인식/수기 초안 구조
│   └── templates/         # index, budgets, budget_create/edit, upload,
│                          # manual_entry, ledger, cart_review, cart_proposal,
│                          # draft_review, 403/404/500
├── data/app.db            # 실제 데이터 (git 제외 권장, 백업 대상)
├── storage/receipts/      # 업로드 원본·정리본·JSON
├── tests/e2e_smoke.py     # 임시 DB 검증 스크립트
└── *.md                   # 설계 문서 (db-schema, input-flow 등 — 참고용)
```

### 라우트
| 경로 | 메서드 | 설명 |
|---|---|---|
| `/` | GET | 예산 현황(잔액 요약) |
| `/budgets`, `/budgets/create`, `/budgets/<id>/edit` | GET/POST | 예산 관리 |
| `/upload` | GET/POST | 영수증/장바구니 업로드 (이미지 `source_file` 선택) |
| `/manual-entry` | GET/POST | 수기 입력 |
| `/ledger` | GET | 지출 장부 (`?budget_id=` 필터) |
| `/transactions/<id>/status` | POST | 상태 변경 (확정/임시/취소) |
| `/transactions/<id>/edit`, `/transactions/<id>/delete` | GET·POST / POST | 거래 수정·삭제 |
| `/transactions/<id>/refund`, `/refunds/<id>/cancel` | GET·POST / POST | 환불 기록·취소 |
| `/cart-review` | GET/POST | 장바구니 입력 → `/cart-proposal`로 이동 |
| `/cart-proposal` | GET | 품의 코드(텍스트/JSON) 복사 |
| `/draft-review` | GET/POST | 초안 검토 |
| `/api/balance`, `/api/ledger` | GET | JSON |

### 테이블 (`database.py`가 기준)
`budget`, `transactions`, `item`, `budget_allocation`, `refund`, `receipt_file`, `request_log`, `cart_proposal`
- 거래 상태: `확정` / `임시` / `취소`. **잔액은 `확정` 거래의 배정액만 차감**, 완료된 환불은 가산.
- 증빙 `file_type`: `원본` / `정리본` / `json`

### 저장 규칙
- 파일명: `일자_예산명_번호_원본.확장자` 등. 금지 문자 제거, 공백은 `_`, 충돌 방지용 짧은 UUID 포함.

---

## 5. 작업 방식 (이 순서로)

1. `python tests/e2e_smoke.py`를 먼저 실행해 `ALL PASS`인지 확인.
2. 수정은 **최소 범위**로. 한 번에 한 가지.
3. 새 동작을 넣으면 `tests/e2e_smoke.py`에 검증을 한 줄 추가.
4. 끝나면 다시 실행해 `ALL PASS` 확인, 그리고 **실제 서버에서 브라우저로** 흐름 확인.
5. 사용자에게는 "무엇이 되고 무엇이 안 되는지"를 쉬운 한국어로 보고.

### 하지 말아야 할 테스트 방식
- 프로젝트 밖에 있던 옛 스크립트(`full_integration_test.sh`, `smoke_test*.sh`, `run_smoke_test.sh`, 저장소에는 없음)는 `kill -9`/`pkill`로 프로세스를 마구 종료하고 **실제 DB를 사용**함. 쓰지 말 것.
- 서버가 도는 중에 `sqlite3` CLI로 `data/app.db`를 직접 열지 말 것.

---

## 6. 사용자 의도 (요약)
- 예산 분류·금액·품목·단가·총액·잔액·거래처를 DB로 관리
- 영수증/장바구니 이미지를 올리면: 원본 저장 → (나중에) 인식 결과 반영 자리 → 수기 보완 → 저장
- 한 사람, 한 PC에서 실제로 입력·조회·저장이 되는 것이 최우선
- SQLite = 영구 장부, JSON = 초안/임시/교환 자료
