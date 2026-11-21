import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import app
from supabase_app.routes import _evidence_filename, _normalize_budget_name
from supabase_app import services
from supabase_app import storage


class UploadAttachmentTests(unittest.TestCase):
    def test_cart_image_is_parsed_into_review_table(self):
        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.list_budgets",
                return_value=[{"id": "budget-id", "name": "학급비"}],
            ),
            patch(
                "supabase_app.routes.parse_receipt",
                return_value={
                    "items": [
                        {
                            "description": "교재",
                            "quantity": 2,
                            "unit_price": 5000,
                            "amount": 10000,
                            "price_candidates": [5000],
                            "needs_review": False,
                        }
                    ],
                    "amount": 10000,
                },
            ) as parse,
        ):
            response = app.test_client().post(
                "/cart-review",
                data={
                    "budget_id": "budget-id",
                    "cart_image": (io.BytesIO(b"image"), "cart.png"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("교재".encode(), response.data)
        self.assertIn("구매 완료".encode(), response.data)
        self.assertIn("영수증 (구매 완료 시 필수)".encode(), response.data)
        self.assertNotIn("합계 선택".encode(), response.data)
        parse.assert_called_once()

    def test_cart_requisition_completion_adds_draft_ledger_entry(self):
        review = {
            "budget_id": "budget-id",
            "items": [
                {
                    "description": "교재",
                    "quantity": 2,
                    "unit_price": 5000,
                    "amount": 10000,
                }
            ],
            "items_total": 10000,
            "receipt_total": 10000,
            "notes": "",
        }
        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.list_budgets",
                return_value=[{"id": "budget-id", "name": "학급비"}],
            ),
            patch(
                "supabase_app.routes.services.create_transaction",
                return_value={"id": "tx-id"},
            ) as create,
        ):
            client = app.test_client()
            with client.session_transaction() as state:
                state["cart_image_review"] = review
            response = client.post(
                "/cart-review",
                data={
                    "action": "requisition",
                    "item_description": ["교재 (주문 2개)"],
                    "item_quantity": ["1"],
                    "item_amount": ["10000"],
                    "cart_total": "10000",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(create.call_args.kwargs["amount"], 10000)
        self.assertEqual(create.call_args.kwargs["line_items"][0]["unit_price"], 10000)
        self.assertEqual(create.call_args.kwargs["quantity"], 1)
        self.assertIn("주문 2개", create.call_args.kwargs["description"])
        self.assertEqual(create.call_args.kwargs["status"], services.STATUS_DRAFT)

    def test_cart_completion_rejects_amount_mismatch(self):
        review = {
            "budget_id": "budget-id",
            "items": [
                {
                    "description": "교재",
                    "quantity": 1,
                    "unit_price": 5000,
                    "amount": 5000,
                }
            ],
            "items_total": 5000,
            "receipt_total": 5000,
            "notes": "",
        }
        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.list_budgets",
                return_value=[{"id": "budget-id", "name": "학급비"}],
            ),
            patch("supabase_app.routes.services.create_transaction") as create,
        ):
            client = app.test_client()
            with client.session_transaction() as state:
                state["cart_image_review"] = review
            response = client.post(
                "/cart-review",
                data={
                    "action": "requisition",
                    "item_description": ["교재"],
                    "item_quantity": ["1"],
                    "item_amount": ["5000"],
                    "cart_total": "6000",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("금액이 일치하지 않습니다".encode(), response.data)
        create.assert_not_called()

    def test_cart_purchase_requires_receipt_and_confirms_entry(self):
        review = {
            "budget_id": "budget-id",
            "items": [
                {
                    "description": "교재",
                    "quantity": 1,
                    "unit_price": 5000,
                    "amount": 5000,
                }
            ],
            "items_total": 5000,
            "receipt_total": 5000,
            "notes": "",
        }
        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.list_budgets",
                return_value=[{"id": "budget-id", "name": "학급비"}],
            ),
            patch(
                "supabase_app.routes.storage_upload",
                side_effect=lambda data, name, mime: f"raw/{name}",
            ),
            patch(
                "supabase_app.routes.services.unique_evidence_filename",
                side_effect=lambda name, reserved=None: name,
            ),
            patch(
                "supabase_app.routes.parse_receipt",
                return_value={"amount": 4800, "items": []},
            ),
            patch(
                "supabase_app.routes.services.create_transaction",
                return_value={"id": "tx-id"},
            ) as create,
        ):
            client = app.test_client()
            with client.session_transaction() as state:
                state["cart_image_review"] = review
            rejected = client.post(
                "/cart-review",
                data={
                    "action": "purchase",
                    "item_description": ["교재"],
                    "item_quantity": ["1"],
                    "item_amount": ["5000"],
                    "cart_total": "5000",
                },
            )
            self.assertEqual(rejected.status_code, 200)
            create.assert_not_called()

            response = client.post(
                "/cart-review",
                data={
                    "action": "purchase",
                    "item_description": ["교재"],
                    "item_quantity": ["1"],
                    "item_amount": ["5000"],
                    "cart_total": "5000",
                    "purchase_receipt": (io.BytesIO(b"receipt"), "receipt.pdf"),
                    "purchase_statement": (io.BytesIO(b"statement"), "statement.pdf"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(create.call_args.kwargs["status"], services.STATUS_CONFIRMED)
        self.assertEqual(create.call_args.kwargs["amount"], 4800)
        self.assertTrue(
            create.call_args.kwargs["line_items"][0]["_receipt_total_unread"] is False
        )
        self.assertEqual(
            create.call_args.kwargs["receipt_path"], "raw/학급비_2026-11-21_영수증.pdf"
        )
        self.assertEqual(
            create.call_args.kwargs["additional_document_path"],
            "raw/학급비_2026-11-21_내역서.pdf",
        )

    def test_upload_review_reports_receipt_total_mismatch(self):
        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.list_budgets",
                return_value=[{"id": "budget-id", "name": "학급비"}],
            ),
            patch(
                "supabase_app.routes.parse_receipt",
                return_value={
                    "description": "교재",
                    "amount": 9000,
                    "quantity": 1,
                    "items_total": 31600,
                    "total_difference": -22600,
                    "receipt_date": "2026-11-21",
                    "vendor": "별빛문구",
                    "items": [
                        {
                            "description": "교재",
                            "quantity": 1,
                            "unit_price": 10000,
                            "amount": 10000,
                            "price_candidates": [10000],
                            "needs_review": False,
                        }
                    ],
                },
            ),
            patch(
                "supabase_app.routes.services.unique_evidence_filename",
                side_effect=lambda filename, reserved=None: filename,
            ),
            patch(
                "supabase_app.routes.storage_upload",
                return_value="raw/test/evidence.jpg",
            ),
        ):
            response = app.test_client().post(
                "/upload",
                data={
                    "budget_id": "budget-id",
                    "receipt_file": (io.BytesIO(b"receipt"), "receipt.jpg"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("금액 불일치".encode(), response.data)
        self.assertIn("차액 1,000원".encode(), response.data)

    def test_upload_review_shows_item_and_receipt_total_difference(self):
        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.list_budgets",
                return_value=[{"id": "budget-id", "name": "학급비"}],
            ),
            patch(
                "supabase_app.routes.parse_receipt",
                return_value={
                    "description": "교재",
                    "amount": 9000,
                    "quantity": 1,
                    "items_total": 10000,
                    "receipt_date": "2026-11-21",
                    "vendor": "별빛문구",
                    "items": [
                        {
                            "description": "교재",
                            "quantity": 1,
                            "unit_price": 10000,
                            "amount": 10000,
                        },
                        {
                            "description": "간식",
                            "quantity": 2,
                            "unit_price": 10800,
                            "amount": 21600,
                            "price_candidates": [18800, 10800],
                            "needs_review": False,
                        },
                    ],
                },
            ),
            patch(
                "supabase_app.routes.services.unique_evidence_filename",
                side_effect=lambda filename, reserved=None: filename,
            ),
            patch(
                "supabase_app.routes.storage_upload",
                return_value="raw/test/evidence.jpg",
            ),
        ):
            response = app.test_client().post(
                "/upload",
                data={
                    "budget_id": "budget-id",
                    "receipt_file": (io.BytesIO(b"receipt"), "receipt.jpg"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("품목 합계: ".encode(), response.data)
        self.assertIn("금액 불일치".encode(), response.data)
        self.assertIn(b'value="9000"', response.data)
        self.assertIn(b'value="10800"', response.data)
        self.assertIn(b'value="21600"', response.data)
        self.assertNotIn("가격 선택".encode(), response.data)

    def test_storage_bucket_uses_correct_options_argument(self):
        class FakeStorage:
            def __init__(self):
                self.create_args = None

            def get_bucket(self, _name):
                raise RuntimeError("bucket missing")

            def create_bucket(self, name, **kwargs):
                self.create_args = (name, kwargs)

        class FakeClient:
            def __init__(self):
                self.storage = FakeStorage()

        fake = FakeClient()
        with patch("supabase_app.storage.client", return_value=fake):
            storage._ensure_bucket()

        self.assertEqual(
            fake.storage.create_args, ("receipts", {"options": {"public": False}})
        )

    def test_storage_upload_uses_supported_content_type_header(self):
        class FakeBucket:
            def __init__(self):
                self.args = None

            def upload(self, path, data, file_options):
                self.args = (path, data, file_options)

        class FakeStorage:
            def __init__(self):
                self.bucket = FakeBucket()

            def from_(self, _name):
                return self.bucket

        class FakeClient:
            def __init__(self):
                self.storage = FakeStorage()

        fake = FakeClient()
        with (
            patch("supabase_app.storage._ensure_bucket"),
            patch("supabase_app.storage.client", return_value=fake),
        ):
            path = storage.upload_receipt(b"receipt", "receipt.pdf", "application/pdf")

        self.assertTrue(path.endswith("/evidence.pdf"))
        self.assertRegex(path, r"^raw/[0-9a-f]{12}/evidence\.pdf$")
        self.assertEqual(
            fake.storage.bucket.args[2],
            {"content-type": "application/pdf", "upsert": "false"},
        )

    def test_budget_name_links_to_edit_form_with_amount(self):
        budget = {
            "id": "budget-id",
            "name": "선도교원",
            "amount": 500000,
            "category": "",
        }
        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch("supabase_app.routes.services.list_budgets", return_value=[budget]),
            patch(
                "supabase_app.routes.services.get_budget_balance", return_value=500000
            ),
        ):
            response = app.test_client().get("/budgets")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/budgets/budget-id/edit"', response.data)

        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch("supabase_app.routes.services.get_budget", return_value=budget),
        ):
            edit_response = app.test_client().get("/budgets/budget-id/edit")

        self.assertEqual(edit_response.status_code, 200)
        self.assertIn(b'name="amount" value="500000"', edit_response.data)

    def test_budget_creation_rejects_existing_name(self):
        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.list_budgets",
                return_value=[{"id": "existing", "name": "2026 학급비"}],
            ),
            patch("supabase_app.routes.services.create_budget") as create,
        ):
            response = app.test_client().post(
                "/budgets/new",
                data={"name": "2026학년도 학급비", "amount": "300000", "category": ""},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("같거나 비슷한 이름의 예산".encode(), response.data)
        create.assert_not_called()

    def test_budget_name_normalization_detects_year_style_variants(self):
        self.assertEqual(
            _normalize_budget_name("2026 학급비"),
            _normalize_budget_name("2026학년도 학급비"),
        )

    def test_upload_form_has_clear_controls_for_both_files(self):
        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.list_budgets",
                return_value=[{"id": "budget-id", "name": "학급비"}],
            ),
        ):
            response = app.test_client().get("/upload")

        self.assertEqual(response.status_code, 200)
        self.assertIn("영수증 파일 선택 취소".encode(), response.data)
        self.assertIn("추가 서류 파일 선택 취소".encode(), response.data)
        self.assertIn("input.value = ''".encode(), response.data)

    def test_upload_with_no_detected_items_discards_temp_file_and_parse_data(self):
        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.list_budgets",
                return_value=[{"id": "budget-id", "name": "학급비"}],
            ),
            patch(
                "supabase_app.routes.parse_receipt",
                return_value={"items": [], "amount": ""},
            ),
            patch(
                "supabase_app.routes.services.unique_evidence_filename",
                side_effect=lambda filename, reserved=None: filename,
            ),
            patch(
                "supabase_app.routes.storage_upload",
                return_value="raw/temp/receipt.jpg",
            ),
            patch("supabase_app.routes.storage_delete") as delete,
        ):
            client = app.test_client()
            response = client.post(
                "/upload",
                data={
                    "budget_id": "budget-id",
                    "receipt_file": (io.BytesIO(b"receipt"), "receipt.jpg"),
                },
                content_type="multipart/form-data",
            )
            with client.session_transaction() as state:
                self.assertNotIn("pending_receipt", state)

        self.assertEqual(response.status_code, 302)
        delete.assert_called_once_with("raw/temp/receipt.jpg")

    def test_evidence_filename_has_budget_date_vendor_and_extension(self):
        self.assertEqual(
            _evidence_filename("학급비 1학기", "2026-11-21", "별빛 문구", "jpg"),
            "학급비_1학기_2026-11-21_별빛_문구.jpg",
        )

    def test_duplicate_evidence_names_get_sequential_suffix(self):
        class Query:
            def __init__(self, existing):
                self.existing = existing
                self.candidate = ""

            def select(self, _columns):
                return self

            def or_(self, expression):
                self.candidate = expression.split(".eq.", 1)[1].split(",", 1)[0]
                return self

            def limit(self, _count):
                return self

            def execute(self):
                return SimpleNamespace(
                    data=[{}] if self.candidate in self.existing else []
                )

        class FakeClient:
            def table(self, _name):
                return Query(
                    {"budget_2026-11-20_vendor.pdf", "budget_2026-11-20_vendor_2.pdf"}
                )

        with patch("supabase_app.services.client", return_value=FakeClient()):
            filename = services.unique_evidence_filename("budget_2026-11-20_vendor.pdf")

        self.assertEqual(filename, "budget_2026-11-20_vendor_3.pdf")

    def test_filename_check_falls_back_when_optional_db_column_is_missing(self):
        class Query:
            def select(self, _columns):
                return self

            def or_(self, _expression):
                return self

            def eq(self, _column, _value):
                return self

            def limit(self, _count):
                return self

            def execute(self):
                if hasattr(self, "used_fallback"):
                    return SimpleNamespace(data=[])
                self.used_fallback = True
                raise RuntimeError("column additional_document_filename does not exist")

        class FakeClient:
            def table(self, _name):
                return Query()

        with patch("supabase_app.services.client", return_value=FakeClient()):
            filename = services.unique_evidence_filename("budget_2026-11-20_vendor.pdf")

        self.assertEqual(filename, "budget_2026-11-20_vendor.pdf")

    def test_optional_document_is_saved_with_receipt_transaction(self):
        budget_id = "budget-test-id"
        upload_path = "https://storage.test/receipt"
        document_path = "https://storage.test/quote"

        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.list_budgets",
                return_value=[{"id": budget_id, "name": "학급비"}],
            ),
            patch(
                "supabase_app.routes.parse_receipt",
                return_value={
                    "description": "교재",
                    "amount": 12000,
                    "quantity": 1,
                    "receipt_date": "2026-11-20",
                    "vendor": "별빛문구",
                    "items": [
                        {
                            "description": "교재",
                            "quantity": 1,
                            "unit_price": 12000,
                            "amount": 12000,
                        }
                    ],
                },
            ),
            patch(
                "supabase_app.routes.services.unique_evidence_filename",
                side_effect=lambda filename, reserved=None: filename,
            ),
            patch(
                "supabase_app.routes.storage_upload",
                side_effect=[upload_path, document_path],
            ) as upload,
            patch(
                "supabase_app.routes.services.create_transaction",
                return_value={"id": "tx-id"},
            ) as create,
        ):
            client = app.test_client()
            response = client.post(
                "/upload",
                data={
                    "budget_id": budget_id,
                    "receipt_file": (io.BytesIO(b"receipt"), "receipt.jpg"),
                    "additional_document": (io.BytesIO(b"quote"), "quote.pdf"),
                },
                content_type="multipart/form-data",
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn("1개 품목을 읽었습니다".encode(), response.data)
            self.assertIn("학급비_2026-11-20_별빛문구.pdf".encode(), response.data)
            self.assertEqual(upload.call_count, 2)

            response = client.post(
                "/upload",
                data={
                    "action": "save",
                    "item_description": ["교재"],
                    "item_quantity": ["1"],
                    "item_unit_price": ["12000"],
                    "item_amount": ["12000"],
                    "receipt_total": "12000",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(create.call_args.kwargs["receipt_path"], upload_path)
        self.assertEqual(
            create.call_args.kwargs["additional_document_path"], document_path
        )
        self.assertEqual(
            create.call_args.kwargs["additional_document_filename"],
            "학급비_2026-11-20_별빛문구.pdf",
        )
        self.assertEqual(
            create.call_args.kwargs["line_items"],
            [
                {
                    "description": "교재",
                    "quantity": 1,
                    "unit_price": 12000.0,
                    "amount": 12000.0,
                }
            ],
        )
        self.assertEqual(create.call_args.kwargs["description"], "교재")
        self.assertEqual(create.call_args.kwargs["amount"], 12000.0)
        self.assertEqual(create.call_args.kwargs["quantity"], 1)

    def test_transaction_detail_shows_both_documents(self):
        transaction = {
            "id": "tx-id",
            "description": "교재",
            "budget_id": "budget-id",
            "amount": 23000,
            "quantity": 1,
            "unit_price": None,
            "category": "",
            "status": "draft",
            "refund_amount": None,
            "refund_reason": "",
            "notes": "",
            "created_at": "2026-11-21",
            "updated_at": "2026-11-21",
            "receipt_path": "https://storage.test/receipt",
            "receipt_filename": "학급비_2026-11-20_별빛문구.jpg",
            "additional_document_path": "https://storage.test/quote",
            "additional_document_filename": "학급비_2026-11-20_별빛문구.pdf",
            "line_items": [
                {
                    "description": "교과서",
                    "quantity": 2,
                    "unit_price": 10000,
                    "amount": 20000,
                },
                {
                    "description": "공책",
                    "quantity": 1,
                    "unit_price": 3000,
                    "amount": 3000,
                },
            ],
        }

        with (
            patch.dict(os.environ, {"ACCESS_PASSWORD": ""}),
            patch(
                "supabase_app.routes.services.get_transaction", return_value=transaction
            ),
        ):
            response = app.test_client().get("/transactions/tx-id")

        self.assertEqual(response.status_code, 200)
        self.assertIn("학급비_2026-11-20_별빛문구.jpg".encode(), response.data)
        self.assertIn("학급비_2026-11-20_별빛문구.pdf".encode(), response.data)
        self.assertIn("교과서".encode(), response.data)
        self.assertIn("20,000원".encode(), response.data)
        self.assertIn("검증 완료".encode(), response.data)


if __name__ == "__main__":
    unittest.main()
