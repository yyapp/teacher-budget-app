"""임시 DB로 전체 흐름(예산·업로드·자동 인식·확인·확정·환불·수정·삭제)을 검증한다.
실제 data/app.db와 외부 API는 건드리지 않는다. 실행: python tests/e2e_smoke.py (프로젝트 루트에서)"""
import sys, io, os, tempfile, sqlite3, importlib.util, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp()
import app.database as d, app.storage as s
d.DB_PATH = os.path.join(tmp, "t.db")
s.STORAGE_ROOT = tmp; s.RECEIPTS_DIR = os.path.join(tmp, "receipts")
spec = importlib.util.spec_from_file_location("entry", os.path.join(ROOT, "app.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import app.routes as rt
from app.upstage_client import interpret_extraction
c = m.app.test_client()
ok = lambda name, cond: print(("PASS " if cond else "FAIL ") + name) or cond
res = []

def db():
    return sqlite3.connect(d.DB_PATH)
def cnt(table):
    return db().execute(f"select count(*) from {table}").fetchone()[0]
def bal():
    return c.get("/api/balance").get_json()[0]["balance"]
def latest():
    return db().execute("select transaction_id, status, merchant_name, total_amount from transactions order by created_at desc, rowid desc limit 1").fetchone()
def has(resp, text):
    return text.encode() in resp.data

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"

# ---------------- 화면 열림 · 예산 ----------------
for p in ["/", "/budgets", "/upload", "/cart-review", "/ledger", "/api/balance", "/api/ledger", "/manual-entry"]:
    res.append(ok(f"GET {p}", c.get(p).status_code == 200))
res.append(ok("예산 없을 때 업로드 안내", has(c.get("/upload"), "예산을 먼저 등록")))
r = c.post("/budgets/create", data={"budget_name": "학급운영비", "year": "2026", "type": "확정", "allocated_amount": "1000000"})
res.append(ok("POST budgets/create 302", r.status_code == 302))
bid = db().execute("select budget_id from budget").fetchone()[0]
res.append(ok("예산 배정액 음수 거부", has(c.post("/budgets/create", data={"budget_name": "n", "year": "2026", "type": "확정", "allocated_amount": "-1"}, follow_redirects=True), "0 이상")))

# ---------------- 수기 입력으로 거래 만들기 ----------------
manual = {"budget_id": bid, "merchant_name": "다이소", "transaction_date": "2026-11-05", "total_amount": "12000",
          "item_name": ["공책", "연필"], "item_specification": ["", ""], "item_quantity": ["2", "3"], "item_unit": ["권", "자루"],
          "item_unit_price": ["2000", "2666"], "item_amount": ["4000", "8000"]}
r = c.post("/manual-entry", data=manual)
res.append(ok(f"수기 입력 저장 ({r.status_code})", r.status_code == 302))
tid = latest()[0]
res.append(ok(f"DB 거래={cnt('transactions')} 품목={cnt('item')} 배정={cnt('budget_allocation')}", (cnt("transactions"), cnt("item"), cnt("budget_allocation")) == (1, 2, 1)))
res.append(ok("ledger에 다이소 표시", has(c.get("/ledger"), "다이소")))
res.append(ok("수기입력 총액 글자 거부", has(c.post("/manual-entry", data={**manual, "total_amount": "zz"}, follow_redirects=True), "숫자로 입력")))
res.append(ok("수기입력 음수 거부", has(c.post("/manual-entry", data={**manual, "total_amount": "-5"}, follow_redirects=True), "0 이상")))
res.append(ok("수량에 글자 거부", has(c.post("/manual-entry", data={**manual, "item_quantity": ["x", "1"]}, follow_redirects=True), "수량")))
res.append(ok("쉼표 포함 금액 허용", c.post("/manual-entry", data={**manual, "merchant_name": "쉼표상점", "total_amount": "12,000"}).status_code == 302 and latest()[3] == 12000.0))
c.post(f"/transactions/{latest()[0]}/delete")  # 위 쉼표 거래 정리

# ---------------- 장바구니 ----------------
form2 = {"merchant_name": "쿠팡", "document_date": "2026-11-06", "expected_total_amount": "30000", "shipping_fee": "0", "discount_terms": "",
         "item_name": ["색연필"], "item_specification": [""], "item_quantity": ["3"], "item_unit": ["개"], "item_unit_price": ["10000"], "item_amount": ["30000"]}
r = c.post("/cart-review", data=form2)
res.append(ok("장바구니 → 품의 코드 화면", r.status_code == 302 and "id=" in r.headers["Location"] and "cart=" not in r.headers["Location"]))
page = c.get(r.headers["Location"])
res.append(ok("품의 코드 화면에 내용", page.status_code == 200 and has(page, "쿠팡") and has(page, "색연필")))
res.append(ok("장바구니 배송비 글자 거부", has(c.post("/cart-review", data={"merchant_name": "x", "shipping_fee": "abc"}, follow_redirects=True), "배송비")))
many = {"merchant_name": "대량", "expected_total_amount": "1", "item_name": [f"품목{i:03d}" for i in range(300)], "item_quantity": ["1"] * 300, "item_unit_price": ["1"] * 300, "item_amount": ["1"] * 300}
r = c.post("/cart-review", data=many)
page = c.get(r.headers["Location"]).data.decode()
res.append(ok(f"품목 300개 장바구니 (주소 {len(r.headers['Location'])}자)", len(r.headers["Location"]) < 200 and "품목000" in page and "품목299" in page))
res.append(ok("예전 ?cart= 주소 호환", has(c.get("/cart-proposal?cart=" + json.dumps({"merchant_name": "구형상점", "items": [{"item_name": "a", "item_amount": 1}]}, ensure_ascii=False)), "구형상점")))
res.append(ok("없는 장바구니 id 안내", has(c.get("/cart-proposal?id=nope", follow_redirects=True), "찾을 수 없습니다")))
res.append(ok("빈 장바구니 화면에 복사 칸 없음", "텍스트 복사" not in c.get("/cart-proposal").data.decode()))
res.append(ok("빈 초안 화면 정상", c.get("/draft-review").status_code == 200))

# ---------------- 상태 변경 · 잔액 ----------------
bal0 = bal()
res.append(ok("확정 처리", c.post(f"/transactions/{tid}/status", data={"status": "확정"}).status_code == 302))
res.append(ok(f"확정 후 잔액 {bal0:.0f} -> {bal():.0f}", bal0 - bal() == 12000))
res.append(ok("장부에 확정 안내", has(c.get("/ledger", follow_redirects=True), "확정")))
c.post(f"/transactions/{tid}/status", data={"status": "취소"})
res.append(ok("취소 후 잔액 복구", bal() == bal0))
res.append(ok("잘못된 상태 거부", has(c.post(f"/transactions/{tid}/status", data={"status": "x"}, follow_redirects=True), "허용되지")))
res.append(ok("없는 거래 상태 변경 거부", has(c.post("/transactions/nope/status", data={"status": "확정"}, follow_redirects=True), "찾을 수")))

# ---------------- 환불 ----------------
c.post(f"/transactions/{tid}/status", data={"status": "확정"})
base = bal()
res.append(ok("환불 화면 열림", c.get(f"/transactions/{tid}/refund").status_code == 200))
res.append(ok("환불 5000 기록", has(c.post(f"/transactions/{tid}/refund", data={"refund_date": "2026-11-07", "refund_amount": "5000"}, follow_redirects=True), "기록했습니다")))
res.append(ok("환불 후 잔액 +5000", bal() == base + 5000))
res.append(ok("장부에 환불 표시", has(c.get("/ledger"), "환불 -5,000")))
res.append(ok("한도 초과 환불 거부", has(c.post(f"/transactions/{tid}/refund", data={"refund_amount": "7001"}, follow_redirects=True), "넘을 수 없습니다")))
res.append(ok("0원·음수·글자 환불 거부", all(has(c.post(f"/transactions/{tid}/refund", data={"refund_amount": v}, follow_redirects=True), msg) for v, msg in (("0", "0보다"), ("-1", "0 이상"), ("x", "숫자")))))
res.append(ok("거부 후 잔액 변화 없음", bal() == base + 5000))
c.post(f"/refunds/{db().execute('select refund_id from refund').fetchone()[0]}/cancel")
res.append(ok("환불 취소 → 잔액 복구", bal() == base))
c.post(f"/transactions/{tid}/status", data={"status": "임시"})
res.append(ok("임시 거래 환불 거부", has(c.post(f"/transactions/{tid}/refund", data={"refund_amount": "1"}, follow_redirects=True), "확정된 거래만")))

# ---------------- 수정 · 삭제 ----------------
bal_before = bal()
page = c.get(f"/transactions/{tid}/edit")
res.append(ok("수정 화면에 기존 값", page.status_code == 200 and has(page, "다이소") and has(page, "공책")))
r = c.post(f"/transactions/{tid}/edit", data={"budget_id": bid, "merchant_name": "알파문구", "transaction_date": "2026-11-08", "total_amount": "9000",
           "item_name": ["스케치북"], "item_quantity": ["3"], "item_unit": ["권"], "item_unit_price": ["3000"], "item_amount": ["9000"]}, follow_redirects=True)
res.append(ok("거래 수정 저장", has(r, "저장했습니다") and has(r, "알파문구")))
res.append(ok("품목 교체·배정 총액 동기화", db().execute("select count(*), min(item_name) from item where transaction_id=?", (tid,)).fetchone() == (1, "스케치북")
              and db().execute("select allocated_amount from budget_allocation where transaction_id=?", (tid,)).fetchone()[0] == 9000))
res.append(ok("수정 시 숫자 검증", has(c.post(f"/transactions/{tid}/edit", data={"budget_id": bid, "merchant_name": "a", "total_amount": "abc"}, follow_redirects=True), "숫자로 입력")))
c.post(f"/transactions/{tid}/status", data={"status": "확정"})
c.post(f"/transactions/{tid}/refund", data={"refund_amount": "4000"})
res.append(ok("환불액보다 작은 총액 거부", has(c.post(f"/transactions/{tid}/edit", data={"budget_id": bid, "merchant_name": "a", "total_amount": "3000"}, follow_redirects=True), "작을 수 없습니다")))
res.append(ok("확정 거래 삭제 거부", has(c.post(f"/transactions/{tid}/delete", follow_redirects=True), "확정된 거래는")))
res.append(ok("환불 있는 확정 거래를 임시로 못 바꿈", has(c.post(f"/transactions/{tid}/status", data={"status": "임시"}, follow_redirects=True), "환불을 먼저 취소")))
b = c.get("/api/balance").get_json()[0]
res.append(ok("잔액이 배정액을 넘지 않음", b["balance"] <= b["allocated_amount"]))
x = db(); x.execute("UPDATE transactions SET status='임시' WHERE transaction_id=?", (tid,)); x.commit()   # 어긋난 예전 데이터를 흉내 낸다
b = c.get("/api/balance").get_json()[0]
res.append(ok("어긋난 데이터에서도 환불을 잔액에 더하지 않음", b["balance"] <= b["allocated_amount"]))
active_refund = db().execute("select refund_id from refund where status='완료'").fetchone()[0]
c.post(f"/refunds/{active_refund}/cancel")
r = c.post(f"/transactions/{tid}/delete", follow_redirects=True)
res.append(ok("임시 거래 삭제", has(r, "삭제했습니다") and db().execute("select count(*) from transactions where transaction_id=?", (tid,)).fetchone()[0] == 0))
res.append(ok("딸린 품목·배정·환불 함께 삭제", all(db().execute(f"select count(*) from {t} where {col}=?", (tid,)).fetchone()[0] == 0 for t, col in (("item", "transaction_id"), ("budget_allocation", "transaction_id"), ("refund", "original_transaction_id")))))
res.append(ok("삭제 뒤 잔액 정상", bal() == bal_before))
res.append(ok("없는 거래 삭제 안내", has(c.post("/transactions/nope/delete", follow_redirects=True), "찾을 수 없습니다")))

# ---------------- 업로드: 예산 + 파일만 → 자동 인식 → 확인 → 확정 (외부 API는 가짜) ----------------
calls = []
def fake_ok(image, key):
    calls.append((key, image[:5]))
    return {"ok": True, "merchant": "행복문구점", "date": "2026-11-05", "total_amount": 12000.0,
            "items": [{"item_name": "공책", "specification": "A5", "quantity": 2.0, "unit": "권", "unit_price": 2000.0, "item_amount": 4000.0, "selection_status": "선택"},
                      {"item_name": "색연필", "specification": "", "quantity": 1.0, "unit": "개", "unit_price": 8000.0, "item_amount": 8000.0, "selection_status": "선택"}]}
rt.parse_receipt_image = fake_ok
os.environ["UPSTAGE_API_KEY"] = "test-key"
def upload(file):
    return c.post("/upload", data={"budget_id": bid, "source_file": file}, content_type="multipart/form-data")

page = c.get("/upload").data.decode()
res.append(ok("업로드 화면은 예산+파일만", all(k in page for k in ('name="budget_id"', 'name="source_file"')) and not any(k in page for k in ('name="merchant_name"', 'name="total_amount"', 'name="item_name"')) and "application/pdf" in page))
n = cnt("transactions")
res.append(ok("없는 예산으로 업로드 거부", has(c.post("/upload", data={"budget_id": "nope", "source_file": (io.BytesIO(PNG), "r.png")}, content_type="multipart/form-data", follow_redirects=True), "예산을 선택")))
res.append(ok("파일 없이 업로드 거부", has(c.post("/upload", data={"budget_id": bid, "source_file": (io.BytesIO(b""), "")}, content_type="multipart/form-data", follow_redirects=True), "파일을 선택")))
res.append(ok("거부 시 거래가 늘지 않음", cnt("transactions") == n))

r = upload((io.BytesIO(PNG), "영수증.png"))
res.append(ok("업로드 → 확인 화면으로 이동", r.status_code == 302 and "/edit" in r.headers["Location"] and "new=1" in r.headers["Location"]))
res.append(ok("인식 API 키·파일 전달", calls == [("test-key", PNG[:5])]))
tid2, status2, merchant2, total2 = latest()
res.append(ok(f"임시 거래로 저장 {status2} {merchant2} {total2}", (status2, merchant2, total2) == ("임시", "행복문구점", 12000.0) and db().execute("select count(*) from item where transaction_id=?", (tid2,)).fetchone()[0] == 2))
page = c.get(r.headers["Location"])
html = page.data.decode()
res.append(ok("확인 화면: 인식 결과", page.status_code == 200 and all(x in html for x in ("인식 결과 확인", "행복문구점", "2026-11-05", "공책", "색연필", "A5"))))
res.append(ok("확인 화면: 수량·단가·금액·총액 표시", all(x in html for x in ('value="2"', 'value="2000"', 'value="4000"', 'value="8000"', 'value="12000"')) and "2000.0" not in html))
res.append(ok("확인 화면: 확정·임시 저장 버튼", 'value="confirm"' in html and "임시 저장" in html))
rid = db().execute("select receipt_id from receipt_file where transaction_id=?", (tid2,)).fetchone()[0]
res.append(ok("확인 화면: 원본 미리보기", f"/receipts/{rid}/file" in html and "<img" in html))
f = c.get(f"/receipts/{rid}/file")
res.append(ok("원본 파일 보기(이미지)", f.status_code == 200 and f.mimetype == "image/png" and f.data == PNG))
res.append(ok("없는 증빙 파일은 404", c.get("/receipts/nope/file").status_code == 404))
x = db(); fp_real = x.execute("select file_path from receipt_file where receipt_id=?", (rid,)).fetchone()[0]
x.execute("update receipt_file set file_path=? where receipt_id=?", ("/etc/hosts", rid)); x.commit()
res.append(ok("저장 폴더 밖 경로는 404", c.get(f"/receipts/{rid}/file").status_code == 404))
x.execute("update receipt_file set file_path=? where receipt_id=?", (fp_real, rid)); x.commit()
res.append(ok("장부에서 원본 링크", has(c.get("/ledger"), f"/receipts/{rid}/file")))

# 수정 후 확정
b0 = bal()
edit = {"action": "confirm", "budget_id": bid, "merchant_name": "행복문구점", "transaction_date": "2026-11-05", "total_amount": "13000",
        "item_name": ["공책", "색연필"], "item_specification": ["A5", ""], "item_quantity": ["3", "1"], "item_unit": ["권", "개"],
        "item_unit_price": ["2000", "7000"], "item_amount": ["6000", "7000"]}
r = c.post(f"/transactions/{tid2}/edit?new=1", data=edit, follow_redirects=True)
res.append(ok("고쳐서 확정", has(r, "확정했습니다") and latest()[1:] == ("확정", "행복문구점", 13000.0)))
res.append(ok(f"확정 후 잔액 {b0:.0f} -> {bal():.0f} (수정한 총액 기준)", b0 - bal() == 13000))
res.append(ok("수정한 품목 반영", db().execute("select quantity from item where transaction_id=? and item_name='공책'", (tid2,)).fetchone()[0] == 3.0))
res.append(ok("확정 거래의 확인 화면엔 확정 버튼 없음", 'value="confirm"' not in c.get(f"/transactions/{tid2}/edit").data.decode()))

# 환불이 있는 확정 거래는 상태 변경 불가 (이전 버그 회귀)
c.post(f"/transactions/{tid2}/refund", data={"refund_amount": "1000"})
res.append(ok("환불 있는 확정 거래를 임시로 못 바꿈(업로드 거래)", has(c.post(f"/transactions/{tid2}/status", data={"status": "임시"}, follow_redirects=True), "환불을 먼저 취소")))

# 총액 0이면 확정 막기
r = upload((io.BytesIO(PNG), "r2.png"))
tid3 = latest()[0]
r = c.post(f"/transactions/{tid3}/edit?new=1", data={**edit, "total_amount": "0", "item_amount": ["0", "0"], "item_unit_price": ["0", "0"]}, follow_redirects=True)
res.append(ok("총액 0이면 확정 안 됨(저장은 됨)", has(r, "총액이 0원") and latest()[1] == "임시"))
c.post(f"/transactions/{tid3}/delete")

# PDF
calls.clear()
r = upload((io.BytesIO(PDF), "영수증.pdf"))
tid4 = latest()[0]
res.append(ok("PDF 업로드 → 인식 → 확인 화면", r.status_code == 302 and calls == [("test-key", b"%PDF-")]))
fname, fpath4, rid4 = db().execute("select file_name, file_path, receipt_id from receipt_file where transaction_id=?", (tid4,)).fetchone()
res.append(ok(f"PDF 증빙 저장 {fname}", fname == "학급운영비_2026-11-05_행복문구점.pdf" and os.path.exists(fpath4) and os.path.basename(fpath4) == fname))
html = c.get(r.headers["Location"]).data.decode()
f = c.get(f"/receipts/{rid4}/file")
res.append(ok("PDF 미리보기(iframe)·파일 보기", "<iframe" in html and f.status_code == 200 and f.mimetype == "application/pdf" and f.data == PDF))
c.post(f"/transactions/{tid4}/delete")
res.append(ok("삭제 시 증빙 파일도 삭제", not os.path.exists(fpath4)))

# 인식 실패 · 키 없음
rt.parse_receipt_image = lambda image, key: {"ok": False, "error": "서버 오류"}
r = upload((io.BytesIO(PNG), "r3.png"))
tid5 = latest()[0]
page = c.get(r.headers["Location"], follow_redirects=True)
res.append(ok("인식 실패 → 파일은 저장, 직접 입력 안내", r.status_code == 302 and has(page, "자동 인식에 실패") and latest()[1:] == ("임시", "미확인 거래처", 0.0)))
res.append(ok("인식 실패 시 빈 품목 줄 제공", 'name="item_name"' in page.data.decode()))
c.post(f"/transactions/{tid5}/delete")
del os.environ["UPSTAGE_API_KEY"]
r = upload((io.BytesIO(PNG), "r4.png"))
res.append(ok("키 없으면 안내하고 직접 입력", has(c.get(r.headers["Location"], follow_redirects=True), "API 키가 설정되지")))
c.post(f"/transactions/{latest()[0]}/delete")

# 파일 검증
n = cnt("transactions")
def rejected(file, msg):
    return has(c.post("/upload", data={"budget_id": bid, "source_file": file}, content_type="multipart/form-data", follow_redirects=True), msg)
res.append(ok("이상한 확장자 거부", rejected((io.BytesIO(b"hello"), "a.exe"), "PDF 파일만")))
res.append(ok("가짜 png 거부", rejected((io.BytesIO(b"not a png"), "a.png"), "손상")))
res.append(ok("가짜 pdf 거부", rejected((io.BytesIO(b"not a pdf at all"), "x.pdf"), "손상된")))
res.append(ok("11MB 이미지 거부", rejected((io.BytesIO(PNG[:8] + b"0" * (11 * 1024 * 1024)), "big.png"), "너무 큽니다")))
res.append(ok("11MB PDF 거부", rejected((io.BytesIO(b"%PDF-" + b"0" * (11 * 1024 * 1024)), "big.pdf"), "너무 큽니다")))
res.append(ok("검증 실패 시 거래가 늘지 않음", cnt("transactions") == n))

# 인식값 정리
mm = interpret_extraction({"merchant_name": " 가게 ", "transaction_date": "2026.11.5", "total_amount": "12,000원",
                           "items": [{"item_name": "a", "quantity": "2", "item_amount": "4,000"}, {"item_name": ""}, "x", {"item_name": "b", "unit_price": 500}]})
res.append(ok("인식값 정리(날짜·쉼표·빈 품목·단가 보정)", mm["merchant"] == "가게" and mm["date"] == "2026-11-05" and mm["total_amount"] == 12000.0
              and [i["item_name"] for i in mm["items"]] == ["a", "b"] and mm["items"][0]["unit_price"] == 2000.0 and mm["items"][1]["item_amount"] == 500.0))
res.append(ok("잘못된 인식 결과 거부", interpret_extraction("x")["ok"] is False))


# ---------------- 증빙 파일명(예산명_일자_거래처) · 증빙서류 출력 ----------------
def fake_named(image, key):   # 앞선 시험과 이름이 겹치지 않도록 다른 거래처·일자로 인식된다고 가정
    out = fake_ok(image, key); out["merchant"] = "연습상점"; out["date"] = "2026-10-10"; return out
rt.parse_receipt_image = fake_named
os.environ["UPSTAGE_API_KEY"] = "test-key"
def receipt_of(t):
    return db().execute("select receipt_id, file_name, file_path from receipt_file where transaction_id=?", (t,)).fetchone()

upload((io.BytesIO(PNG), "아무이름.png"))
tA = latest()[0]
rA, nameA, pathA = receipt_of(tA)
res.append(ok(f"업로드 직후 파일명 {nameA}", nameA == "학급운영비_2026-10-10_연습상점.png" and os.path.basename(pathA) == nameA and os.path.exists(pathA)))
upload((io.BytesIO(PNG), "또다른.png"))
tB = latest()[0]
rB, nameB, pathB = receipt_of(tB)
res.append(ok(f"같은 예산·일자·거래처는 번호로 구분 {nameB}", nameB == "학급운영비_2026-10-10_연습상점_2.png" and os.path.exists(pathB) and os.path.exists(pathA)))

# 거래처·날짜를 고치면 파일명도 바뀜
edit_b = {"action": "save", "budget_id": bid, "merchant_name": "새/거래처:명", "transaction_date": "2026-12-01", "total_amount": "12000",
          "item_name": ["공책"], "item_quantity": ["1"], "item_unit": ["권"], "item_unit_price": ["12000"], "item_amount": ["12000"]}
c.post(f"/transactions/{tB}/edit", data=edit_b)
rB, nameB2, pathB2 = receipt_of(tB)
res.append(ok(f"거래처·날짜 수정 → 파일명 변경 {nameB2}", nameB2 == "학급운영비_2026-12-01_새거래처명.png" and os.path.exists(pathB2) and not os.path.exists(pathB)))
res.append(ok("파일명이 바뀐 뒤에도 원본 보기 정상", c.get(f"/receipts/{rB}/file").data == PNG))
res.append(ok("이름이 겹쳤던 첫 거래 파일은 그대로", receipt_of(tA)[1] == nameA and os.path.exists(pathA)))
# 일자 비우면 '일자미확인'
c.post(f"/transactions/{tB}/edit", data={**edit_b, "transaction_date": ""})
res.append(ok("일자 없으면 '일자미확인'", receipt_of(tB)[1] == "학급운영비_일자미확인_새거래처명.png"))
c.post(f"/transactions/{tB}/edit", data=edit_b)
# 예산 이름을 바꾸면 파일명도 바뀜
c.post(f"/budgets/{bid}/edit", data={"budget_name": "학급 운영비(수정)", "year": "2026", "type": "확정", "allocated_amount": "1000000"})
res.append(ok(f"예산명 수정 → 파일명 변경 {receipt_of(tA)[1]}", receipt_of(tA)[1] == "학급_운영비(수정)_2026-10-10_연습상점.png" and os.path.exists(receipt_of(tA)[2])))
c.post(f"/budgets/{bid}/edit", data={"budget_name": "학급운영비", "year": "2026", "type": "확정", "allocated_amount": "1000000"})
res.append(ok("예산명 되돌리면 파일명도 원복", receipt_of(tA)[1] == nameA))

# 예전 이름으로 저장된 파일도 증빙 출력 화면을 열면 규칙에 맞게 정리
legacy = os.path.join(os.path.dirname(pathA), "2026-10-10_학급운영비_1_abc123_원본.png")
os.rename(pathA, legacy)
x = db(); x.execute("update receipt_file set file_name=?, file_path=? where receipt_id=?", (os.path.basename(legacy), legacy, rA)); x.commit()
page = c.get(f"/transactions/{tA}/evidence")
res.append(ok("예전 파일명도 증빙 화면에서 정리", receipt_of(tA)[1] == nameA and os.path.exists(receipt_of(tA)[2]) and not os.path.exists(legacy)))

# 증빙서류 출력 화면
html = page.data.decode()
res.append(ok("증빙서류 화면 내용", page.status_code == 200 and all(x in html for x in ("증 빙 서 류", "학급운영비", "2026-10-10", "연습상점", "12,000원", "공책", "색연필", nameA))))
res.append(ok("증빙서류 화면: 원본 이미지·인쇄 버튼", f"/receipts/{rA}/file" in html and "window.print()" in html and "@media print" in html))
res.append(ok("인쇄 시 메뉴·버튼 숨김 설정", "noprint" in html and ".noprint { display: none" in html))
res.append(ok("임시 거래는 확정 권장 안내", "확정한 뒤 출력" in html))
dl = c.get(f"/receipts/{rA}/file?download=1")
disp = dl.headers.get("Content-Disposition", "")
res.append(ok("원본 저장(다운로드) 파일명", dl.status_code == 200 and disp.startswith("attachment") and ("UTF-8''" in disp or "filename=" in disp) and dl.data == PNG))
res.append(ok("다운로드 헤더에 새 파일명 포함", __import__("urllib.parse").parse.quote(nameA) in disp or nameA in disp))
res.append(ok("장부에 증빙 출력 링크", has(c.get("/ledger"), f"/transactions/{tA}/evidence")))
res.append(ok("확인 화면에 파일명·증빙 출력 링크", all(x in c.get(f"/transactions/{tA}/edit").data.decode() for x in (nameA, f"/transactions/{tA}/evidence", "예산명_일자_거래처"))))
res.append(ok("없는 거래 증빙 화면 안내", has(c.get("/transactions/nope/evidence", follow_redirects=True), "찾을 수 없습니다")))
# 확정·환불 반영
c.post(f"/transactions/{tA}/status", data={"status": "확정"})
c.post(f"/transactions/{tA}/refund", data={"refund_amount": "2000"})
html = c.get(f"/transactions/{tA}/evidence").data.decode()
res.append(ok("확정 거래는 경고 없음, 환불·실지출 표시", "확정한 뒤 출력" not in html and "-2,000원" in html and "10,000원" in html))
# PDF 원본
upload((io.BytesIO(PDF), "x.pdf"))
tP = latest()[0]
rP, nameP, pathP = receipt_of(tP)
html = c.get(f"/transactions/{tP}/evidence").data.decode()
res.append(ok(f"PDF 증빙서류 화면 {nameP}", nameP.endswith(".pdf") and "<iframe" in html and "별도 첨부" in html and "원본만 열기" in html))
# 거래 삭제하면 파일도 삭제
c.post(f"/transactions/{tP}/delete")
res.append(ok("삭제 시 이름 바뀐 증빙 파일도 삭제", not os.path.exists(pathP)))
del os.environ["UPSTAGE_API_KEY"]

# ---------------- 공통 메뉴 ----------------
menu_labels = ("예산 현황", "예산 관리", "영수증 업로드", "장바구니 검토", "수기 입력", "지출 장부", "품의 코드 복사")
nav_pages = ["/", "/budgets", "/budgets/create", f"/budgets/{bid}/edit", "/upload", "/manual-entry", "/ledger", "/cart-review", "/cart-proposal",
             "/draft-review", f"/transactions/{tid2}/edit", f"/transactions/{tid2}/refund"]
missing = [p for p in nav_pages if not all(l.encode() in c.get(p).data for l in menu_labels)]
res.append(ok(f"모든 화면에 공통 메뉴 7개 (누락: {missing})", not missing))

print("ALL PASS" if all(res) else "SOME FAILED")
sys.exit(0 if all(res) else 1)
