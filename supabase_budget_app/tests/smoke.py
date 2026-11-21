"""간단 스모크 테스트. Supabase 연결과 기본 쿼리만 확인한다.

사용법:
    python tests/smoke.py

환경변수:
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY 필요.
    실제 DB 데이터가 아닌 연결과 테이블 존재만 확인한다.
"""

import os
import sys


def main() -> None:
    errors: list[str] = []

    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if not supabase_url or not supabase_key:
        errors.append(
            "SUPABASE_URL 또는 SUPABASE_SERVICE_ROLE_KEY가 설정되지 않았습니다."
        )
        print("FAIL: 환경변수 누락")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    # ---- Supabase 클라이언트 연결 테스트 ----
    try:
        from supabase import create_client

        client = create_client(supabase_url, supabase_key)
        print("✓ Supabase 클라이언트 생성 완료")
    except Exception as e:
        errors.append(f"Supabase 클라이언트 생성 실패: {e}")
        print(f"FAIL: {e}")
        sys.exit(1)

    # ---- 테이블 존재 확인 ----
    tables = ["budgets", "transactions", "cart_proposals"]
    for table in tables:
        try:
            client.table(table).select("id", head=True).execute()
            print(f"✓ 테이블 '{table}' 접근 가능")
        except Exception as e:
            errors.append(f"테이블 '{table}' 확인 실패: {e}")

    # ---- Storage 버킷 확인 ----
    try:
        buckets = client.storage.list_buckets()
        bucket_names = [
            b.get("name") if isinstance(b, dict) else b.name for b in buckets
        ]
        if "receipts" not in bucket_names:
            print("⚠ Storage 버킷 'receipts'가 없습니다. Supabase 콘솔에서 생성하세요.")
        else:
            print("✓ Storage 버킷 'receipts' 존재")
    except Exception as e:
        print(f"⚠ Storage 버킷 확인 실패 (없어도 진행 가능): {e}")

    # ---- 결과 ----
    print()
    if errors:
        print(f"FAIL ({len(errors)}개 문제)")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("ALL PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
