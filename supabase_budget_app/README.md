# 개인용 예산·구매 서류 도우미 (Supabase + Flask)

Pil/wri 버전의 로컬 웹앱. Supabase(PostgreSQL, 인증, 스토리지) 기반.

## 스택

- Python 3.14 + Flask + Jinja2
- Supabase (PostgreSQL + Authentication + Storage)
- Railway 배포 예정

## 사전 준비 (Supabase 콘솔)

1. [Supabase 프로젝트 생성](https://supabase.com/dashboard)
2. 프로젝트 설정 → API → **프로젝트 URL**과 **서비스 역할 키(Service role secret)** 확인
3. (옵션) Authentication → 이메일/비밀번호 활성화, 사용자 1명 생성
4. Storage → `receipts` 버킷 생성 (공개/비공개는 보안 맞게 설정)
5. 기존 프로젝트라면 `supabase_app/doc/add-transaction-document-columns.sql`을 SQL Editor에서 한 번 실행 (추가 첨부 파일과 품목별 내역 저장 칸)

## 로컬 실행

```bash
cd supabase_budget_app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env 파일에 아래 값 채우기
FLASK_PORT=5003
SUPABASE_URL=https://YourProject.supabase.co
    SUPABASE_SERVICE_ROLE_KEY=eyJhbG... (서비스 역할 키)
    ACCESS_PASSWORD=개인비밀번호  # Supabase Auth 미사용 시
    UPSTAGE_API_KEY=up_...        # 영수증 OCR 사용 시
    python app.py
```

접속: `http://127.0.0.1:5003`

## Railway 배포

1. Railway 프로젝트 생성
2. GitHub 저장소 연결 (또는 수동 배포)
3. Railway 환경변수 설정:
   - `SUPABASE_URL`
    - `SUPABASE_SERVICE_ROLE_KEY`
    - `ACCESS_PASSWORD` (사용 시)
    - `UPSTAGE_API_KEY` (영수증 OCR 사용 시)
   - `FLASK_PORT` (Railway가 자동으로 할당하지만 명시 가능)
4. 빌드 명령: `pip install -r requirements.txt`
5. 시작 명령: `gunicorn app:app` (또는 `python app.py`)

## 파일 구성

```
supabase_budget_app/
├── app.py                 # Flask 진입점
├── requirements.txt       # 의존성
├── .env.example           # 환경변수 예시
├── .gitignore
├── README.md              # 이 파일
├── Procfile               # Railway/Gunicorn
├── supabase_app/
│   ├── __init__.py
│   ├── db.py              # Supabase 클라이언트
│   ├── forms.py           # 입력 검증
│   ├── routes.py          # 라우트
│   ├── services.py        # 비즈니스 로직
│   ├── storage.py         # Supabase Storage 헬퍼
│   └── templates/         # HTML
│   └── doc/                # DB 초기화·변경 SQL
└── tests/
    └── smoke.py           # 간단 검증
```

## 환경변수

| 변수 | 용도 | 필수 |
|---|---|---|
| `SUPABASE_URL` | 프로젝트 URL (https://xxx.supabase.co) | 예 |
| `SUPABASE_SERVICE_ROLE_KEY` | 서버용 관리자 키 | 예 |
| `ACCESS_PASSWORD` | 단순 접속 비밀번호 (Supabase Auth 미사용 시) | 아니오 |
| `UPSTAGE_API_KEY` | 영수증 OCR 자동 인식 | OCR 사용 시 |
| `FLASK_PORT` | 로컬 포트 (Railway에선 무시) | 아니오 |

> **주의**: `SUPABASE_SERVICE_ROLE_KEY`는 관리자 권한 키이므로 절대 프론트엔드나 클라이언트에 노출하면 안 됩니다. 서버 환경변수로만 사용하세요.

## 라이센스

개인 사용 목적.
