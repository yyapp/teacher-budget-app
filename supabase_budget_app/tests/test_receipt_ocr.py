import os
import json
import unittest
from unittest.mock import patch

import httpx
from supabase_app.receipt_ocr import (
    _ocr_text,
    cart_items_from_ocr,
    extract_cart_fields,
    extract_receipt_fields,
    parse_cart_image,
    parse_receipt,
)


class ReceiptOcrParsingTests(unittest.TestCase):
    @patch("supabase_app.receipt_ocr.httpx.post")
    def test_cart_image_primary_path_uses_visual_context_and_schema(self, post):
        post.return_value.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "items": [
                                    {
                                        "description": "곰돌이푸 짝꿍 미니 수첩",
                                        "option": "",
                                        "package_size": "",
                                        "order_quantity": "18",
                                        "unit_price": "1600",
                                        "item_amount": "28800",
                                        "source_text": "1,600원 18개 28,800원",
                                        "confidence": "high",
                                    },
                                    {
                                        "description": "크리스마스 산타모자 산타분장",
                                        "option": "",
                                        "package_size": "",
                                        "order_quantity": "18",
                                        "unit_price": "1200",
                                        "item_amount": "21600",
                                        "source_text": "1,200원 18개 21,600원",
                                        "confidence": "high",
                                    },
                                ],
                                "products_subtotal": "50400",
                                "shipping": "3000",
                                "discount": "4510",
                                "discount_present": True,
                                "order_amount": "53400",
                                "payable": "48890",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        with patch.dict(os.environ, {"UPSTAGE_API_KEY": "test-key"}):
            result = parse_cart_image(b"cart image", "cart.png", "image/png")

        request = post.call_args.kwargs["json"]
        self.assertEqual(
            post.call_args.args[0],
            "https://api.upstage.ai/v1/information-extraction/chat/completions",
        )
        self.assertEqual(result["extraction_method"], "visual_information_extraction")
        self.assertEqual(
            [item["item_amount"] for item in result["items"]], [28800, 21600]
        )
        self.assertEqual(result["calculated_items_subtotal"], 50400)
        self.assertEqual(result["order_amount"], 53400)
        self.assertEqual(result["calculated_order_amount"], 53400)
        self.assertEqual(result["calculated_payable"], 48890)
        self.assertEqual(result["order_amount_status"], "matched")
        self.assertIn("상품 카드", request["messages"][0]["content"])

    @patch("supabase_app.receipt_ocr.httpx.post")
    def test_visual_low_confidence_keeps_item_unverified(self, post):
        post.return_value.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "items": [
                                    {
                                        "description": "과자",
                                        "option": "",
                                        "package_size": "",
                                        "order_quantity": "18",
                                        "unit_price": "1600",
                                        "item_amount": "28800",
                                        "source_text": "가격 영역",
                                        "confidence": "low",
                                    }
                                ],
                                "products_subtotal": "28800",
                                "shipping": "0",
                                "discount": "0",
                                "discount_present": True,
                                "payable": "28800",
                            }
                        )
                    }
                }
            ]
        }
        with patch.dict(os.environ, {"UPSTAGE_API_KEY": "test-key"}):
            result = parse_cart_image(b"cart image", "cart.png", "image/png")

        self.assertTrue(result["items"][0]["needs_review"])
        self.assertEqual(result["products_status"], "review_required")

    @patch("supabase_app.receipt_ocr.parse_receipt")
    @patch(
        "supabase_app.receipt_ocr.httpx.post",
        side_effect=httpx.ConnectError("service unavailable"),
    )
    def test_cart_context_service_failure_uses_ocr_fallback(
        self, _post, parse_receipt_mock
    ):
        parse_receipt_mock.return_value = extract_cart_fields(
            "수첩\n1,600원 18개 28,800원\n상품 합계 28,800원\n배송비 0원\n주문 금액 28,800원"
        )
        with patch.dict(os.environ, {"UPSTAGE_API_KEY": "test-key"}):
            result = parse_cart_image(b"cart image", "cart.png", "image/png")

        self.assertEqual(result["extraction_method"], "ocr_fallback")
        self.assertEqual(result["items"][0]["item_amount"], 28800)

    def test_rebuilds_visual_rows_from_ocr_word_positions(self):
        text = _ocr_text(
            {
                "pages": [
                    {
                        "height": 1000,
                        "words": [
                            {
                                "text": "공책",
                                "boundingBox": {
                                    "vertices": [
                                        {"x": 10, "y": 100},
                                        {"x": 30, "y": 100},
                                        {"x": 30, "y": 120},
                                        {"x": 10, "y": 120},
                                    ]
                                },
                            },
                            {
                                "text": "3,000원",
                                "boundingBox": {
                                    "vertices": [
                                        {"x": 200, "y": 101},
                                        {"x": 260, "y": 101},
                                        {"x": 260, "y": 121},
                                        {"x": 200, "y": 121},
                                    ]
                                },
                            },
                            {
                                "text": "연필",
                                "boundingBox": {
                                    "vertices": [
                                        {"x": 10, "y": 150},
                                        {"x": 30, "y": 150},
                                        {"x": 30, "y": 170},
                                        {"x": 10, "y": 170},
                                    ]
                                },
                            },
                            {
                                "text": "1,000원",
                                "boundingBox": {
                                    "vertices": [
                                        {"x": 200, "y": 151},
                                        {"x": 260, "y": 151},
                                        {"x": 260, "y": 171},
                                        {"x": 200, "y": 171},
                                    ]
                                },
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(text.splitlines(), ["공책 3,000원", "연필 1,000원"])

    def test_positioned_words_are_used_even_when_flat_text_exists(self):
        text = _ocr_text(
            {
                "text": "상품1 1,600원 18개 28,800원 상품2 1,200원 18개 21,600원",
                "pages": [
                    {
                        "height": 1000,
                        "text": "상품1 1,600원 18개 28,800원 상품2 1,200원 18개 21,600원",
                        "words": [
                            {
                                "text": "상품1",
                                "boundingBox": {
                                    "vertices": [
                                        {"x": 10, "y": 100},
                                        {"x": 35, "y": 100},
                                        {"x": 35, "y": 120},
                                        {"x": 10, "y": 120},
                                    ]
                                },
                            },
                            {
                                "text": "상품2",
                                "boundingBox": {
                                    "vertices": [
                                        {"x": 10, "y": 150},
                                        {"x": 35, "y": 150},
                                        {"x": 35, "y": 170},
                                        {"x": 10, "y": 170},
                                    ]
                                },
                            },
                        ],
                    }
                ],
            }
        )

        self.assertEqual(text, "상품1\n상품2")

    def test_cart_handover_two_items_and_independent_order_totals(self):
        fields = extract_cart_fields(
            "장바구니 목록\n바로구매\n옵션저장\n"
            "곰돌이푸 짝꿍 미니 수첩\n1,600원 18개 28,800원\n"
            "크리스마스 산타모자 산타분장\n1,200원 18개 21,600원\n"
            "총 상품 금액 50,400원\n배송비 3,000원\n주문 금액 53,400원\n결제 예정 금액 53,400원"
        )

        self.assertEqual(
            [item["description"] for item in fields["items"]],
            ["곰돌이푸 짝꿍 미니 수첩", "크리스마스 산타모자 산타분장"],
        )
        self.assertEqual([item["order_quantity"] for item in fields["items"]], [18, 18])
        self.assertEqual([item["unit_price"] for item in fields["items"]], [1600, 1200])
        self.assertEqual(
            [item["item_amount"] for item in fields["items"]], [28800, 21600]
        )
        self.assertEqual(fields["products_subtotal"], 50400)
        self.assertEqual(fields["calculated_items_subtotal"], 50400)
        self.assertEqual(fields["shipping"], 3000)
        self.assertEqual(fields["discount"], 0)
        self.assertEqual(fields["order_amount"], 53400)
        self.assertEqual(fields["calculated_order_amount"], 53400)
        self.assertEqual(fields["order_amount_status"], "matched")
        self.assertEqual(fields["payable"], 53400)
        self.assertEqual(fields["calculated_payable"], 53400)
        self.assertEqual(fields["products_status"], "matched")
        self.assertEqual(fields["payable_status"], "matched")

    def test_cart_preserves_package_size_separately_from_order_quantity(self):
        fields = extract_cart_fields("[간식] 초코 과자 20개입\n2,720원 18개 48,960원")

        self.assertEqual(fields["items"][0]["package_size"], "20개입")
        self.assertEqual(fields["items"][0]["order_quantity"], 18)
        self.assertEqual(fields["items"][0]["item_amount"], 48960)

    @patch("supabase_app.receipt_ocr.httpx.post")
    def test_cart_image_uses_visual_information_extraction_schema(self, post):
        post.return_value.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"items":['
                            '{"description":"곰돌이푸 짝꿍 미니 수첩","option":"","package_size":"","order_quantity":"18","unit_price":"1600","item_amount":"28800","source_text":"1,600원 18개 28,800원","confidence":"high"},'
                            '{"description":"크리스마스 산타모자 산타분장","option":"","package_size":"","order_quantity":"18","unit_price":"1200","item_amount":"21600","source_text":"1,200원 18개 21,600원","confidence":"high"}'
                            '],"products_subtotal":"50400","shipping":"3000","discount":"4510","discount_present":true,"order_amount":"53400","payable":"48890"}'
                        )
                    }
                }
            ]
        }
        with patch.dict(os.environ, {"UPSTAGE_API_KEY": "test-key"}):
            result = parse_cart_image(b"image-bytes", "cart.png", "image/png")

        self.assertEqual(result["extraction_method"], "visual_information_extraction")
        self.assertEqual(
            [item["description"] for item in result["items"]],
            ["곰돌이푸 짝꿍 미니 수첩", "크리스마스 산타모자 산타분장"],
        )
        self.assertEqual([item["order_quantity"] for item in result["items"]], [18, 18])
        self.assertEqual(
            [item["item_amount"] for item in result["items"]], [28800, 21600]
        )
        self.assertEqual(result["products_status"], "matched")
        self.assertEqual(result["payable_status"], "matched")
        self.assertEqual(result["order_amount"], 53400)
        self.assertEqual(result["calculated_order_amount"], 53400)
        self.assertEqual(result["order_amount_status"], "matched")
        self.assertEqual(
            post.call_args.args[0],
            "https://api.upstage.ai/v1/information-extraction/chat/completions",
        )

    @patch("supabase_app.receipt_ocr.parse_receipt")
    @patch(
        "supabase_app.receipt_ocr.httpx.post",
        side_effect=RuntimeError("model unavailable"),
    )
    def test_cart_image_falls_back_to_ocr_when_context_model_unavailable(
        self, _post, parse_receipt_mock
    ):
        parse_receipt_mock.return_value = extract_cart_fields(
            "수첩\n1,600원 18개 28,800원\n상품 합계 28,800원\n배송비 0원\n주문 금액 28,800원"
        )
        with patch.dict(os.environ, {"UPSTAGE_API_KEY": "test-key"}):
            result = parse_cart_image(b"image-bytes", "cart.png", "image/png")

        self.assertEqual(result["extraction_method"], "ocr_fallback")
        self.assertEqual(result["items"][0]["item_amount"], 28800)

    def test_cart_missing_original_summaries_are_not_reported_as_verified(self):
        fields = extract_cart_fields(
            "수첩\n1,600원 18개 28,800원\n모자\n1,200원 18개 21,600원"
        )

        self.assertEqual(fields["calculated_items_subtotal"], 50400)
        self.assertIsNone(fields["products_subtotal"])
        self.assertEqual(fields["products_status"], "unavailable")
        self.assertIsNone(fields["payable"])
        self.assertEqual(fields["payable_status"], "unavailable")

    def test_extracts_item_totals_and_quantities(self):
        fields = extract_receipt_fields(
            "문구점\n"
            "2026-11-21 12:30\n"
            "연필 2개입\n"
            "1,000원 2개\n"
            "공책\n"
            "3,000원 1개\n"
            "총 결제금액 4,000원"
        )

        self.assertEqual(fields["description"], "연필 2개입, 공책")
        self.assertEqual(fields["amount"], 4000)
        self.assertEqual(fields["quantity"], 3)
        self.assertEqual(
            [
                {
                    key: item[key]
                    for key in ("description", "quantity", "unit_price", "amount")
                }
                for item in fields["items"]
            ],
            [
                {
                    "description": "연필 2개입",
                    "quantity": 2,
                    "unit_price": 500,
                    "amount": 1000,
                },
                {
                    "description": "공책",
                    "quantity": 1,
                    "unit_price": 3000,
                    "amount": 3000,
                },
            ],
        )

    def test_reads_product_name_on_line_above_price(self):
        fields = extract_receipt_fields("교과서\n12,000원 1개\n합계 12,000원")

        self.assertEqual(fields["description"], "교과서")
        self.assertEqual(fields["amount"], 12000)
        self.assertEqual(fields["quantity"], 1)
        self.assertEqual(fields["items"][0]["unit_price"], 12000)

    def test_extracts_unit_price_from_quantity_and_line_amount(self):
        fields = extract_receipt_fields("연필 20개입\n2,000원 2개\n합계 4,000원")

        self.assertEqual(
            [
                {
                    key: item[key]
                    for key in ("description", "quantity", "unit_price", "amount")
                }
                for item in fields["items"]
            ],
            [
                {
                    "description": "연필 20개입",
                    "quantity": 2,
                    "unit_price": 1000,
                    "amount": 2000,
                }
            ],
        )

    def test_extracts_receipt_items_when_ocr_omits_won_suffix(self):
        fields = extract_receipt_fields(
            "교재 구입\n국어 교과서 2개 12,000\n미술 준비물 1개 8,500\n결제금액 32,500"
        )

        self.assertEqual(len(fields["items"]), 2)
        self.assertEqual(fields["items"][0]["description"], "국어 교과서")
        self.assertEqual(fields["items"][0]["quantity"], 2)
        self.assertEqual(fields["items"][0]["unit_price"], 6000)
        self.assertEqual(fields["items"][0]["amount"], 12000)
        self.assertEqual(fields["amount"], 32500)

    def test_ignores_shipping_address_and_delivery_status(self):
        fields = extract_receipt_fields(
            "별빛문구\n"
            "받는분 홍길동\n"
            "서울특별시 강남구 테헤란로 123\n"
            "강남구 역삼로 45\n"
            "배송완료 2026-11-21\n"
            "운송장번호: 1234567890\n"
            "연필 20개입\n"
            "1,000원 2개\n"
            "합계 2,000원"
        )

        self.assertEqual(
            [
                {
                    key: item[key]
                    for key in ("description", "quantity", "unit_price", "amount")
                }
                for item in fields["items"]
            ],
            [
                {
                    "description": "연필 20개입",
                    "quantity": 2,
                    "unit_price": 500,
                    "amount": 1000,
                }
            ],
        )
        self.assertEqual(fields["amount"], 2000)

    def test_korean_item_names_containing_address_syllables_are_kept(self):
        fields = extract_receipt_fields(
            "문구 2개입\n500원 2개\n라면\n1,000원 2개\n합계 3,000원"
        )

        self.assertEqual(
            [item["description"] for item in fields["items"]], ["문구 2개입", "라면"]
        )
        self.assertEqual(fields["amount"], 3000)

    def test_prefers_final_card_total_and_reports_difference(self):
        fields = extract_receipt_fields(
            "교재 1개 10,000원\n합계 10,000원\n할인 1,000원\n실결제금액 9,000원"
        )

        self.assertEqual(fields["items_total"], 10000)
        self.assertEqual(fields["amount"], 9000)
        self.assertEqual(fields["total_difference"], -1000)

    def test_generic_receipt_does_not_force_price_candidates_to_match_total(self):
        fields = extract_receipt_fields(
            "과자A\n18,800원 10,800원 2개\n과자B\n22,000원 1개\n실결제금액 32,800원"
        )

        self.assertIsNone(fields["items"][0]["amount"])
        self.assertTrue(fields["items"][0]["needs_review"])

    def test_excludes_approval_number_from_card_total(self):
        fields = extract_receipt_fields(
            "교재 1개 12,000원\n결제금액 12,000원 승인번호: 12345678"
        )

        self.assertEqual(fields["amount"], 12000)

    def test_marketplace_receipt_uses_purchase_quantity_not_package_size(self):
        fields = extract_receipt_fields(
            "서울특별시 구로구 부일로 893\n"
            "[킨더] 초콜릿비 미니 20개입\n"
            "9,560원 2개\n"
            "[오리온] 고래범 초코송이 미니 믹스 20입\n"
            "18,800원 10,800원 | 2개\n"
            "배송완료\n"
            "실결제금액 31,120원"
        )

        self.assertEqual(len(fields["items"]), 2)
        self.assertEqual(fields["items"][0]["quantity"], 2)
        self.assertEqual(fields["items"][0]["unit_price"], 4780)
        self.assertEqual(fields["items"][0]["amount"], 9560)
        self.assertEqual(fields["items"][1]["quantity"], 2)
        self.assertIsNone(fields["items"][1]["unit_price"])
        self.assertIsNone(fields["items"][1]["amount"])
        self.assertEqual(fields["items"][1]["price_candidates"], [18800, 10800])
        self.assertEqual(fields["amount"], 31120)

    def test_cart_line_total_is_not_multiplied_by_quantity_again(self):
        parsed = extract_cart_fields("초코 과자 20개입\n2,720원 20개 54,400원")
        cart_items = cart_items_from_ocr(parsed["items"])

        self.assertEqual(cart_items[0]["amount"], 54400)
        self.assertEqual(cart_items[0]["unit_price"], 2720)
        self.assertEqual(cart_items[0]["quantity"], 1)
        self.assertEqual(cart_items[0]["order_quantity"], 20)
        self.assertNotEqual(cart_items[0]["amount"], 1088000)

    def test_order_row_bold_price_is_full_item_amount(self):
        fields = extract_receipt_fields("[오리온] 초코 과자 144g\n54,400원 20개")

        fields = extract_cart_fields("[오리온] 초코 과자 144g\n2,720원 20개 54,400원")
        self.assertEqual(fields["items"][0]["order_quantity"], 20)
        self.assertEqual(fields["items"][0]["item_amount"], 54400)
        self.assertEqual(fields["items"][0]["unit_price"], 2720)

    def test_cart_multiple_unrelated_prices_remain_review_required(self):
        cart_items = cart_items_from_ocr(
            [
                {
                    "description": "과자",
                    "quantity": 20,
                    "amount": "",
                    "price_candidates": [30000, 22000],
                    "needs_review": True,
                }
            ]
        )

        self.assertEqual(cart_items[0]["amount_candidates"], [30000, 22000])
        self.assertEqual(cart_items[0]["amount"], "")
        self.assertIsNone(cart_items[0]["unit_price"])
        self.assertEqual(cart_items[0]["quantity"], 1)

    def test_cart_items_do_not_change_prices_to_match_an_order_total(self):
        cart_items = cart_items_from_ocr(
            [
                {
                    "description": "과자A",
                    "quantity": 2,
                    "price_candidates": [18800, 10800],
                },
                {
                    "description": "과자B",
                    "quantity": 1,
                    "price_candidates": [22000, 30000],
                },
            ],
            final_total=32800,
        )

        self.assertEqual([item["amount"] for item in cart_items], ["", ""])
        self.assertEqual([item["unit_price"] for item in cart_items], [None, None])
        self.assertEqual([item["quantity"] for item in cart_items], [1, 1])

    def test_does_not_guess_receipt_total_from_item_sum(self):
        fields = extract_receipt_fields("공책\n3,000원 2개")

        self.assertEqual(fields["items_total"], 3000)
        self.assertEqual(fields["amount"], "")

    def test_leaves_unrecognized_amount_empty(self):
        fields = extract_receipt_fields("학교 매점\n감사합니다")

        self.assertEqual(fields["amount"], "")
        self.assertEqual(fields["quantity"], 1)

    def test_extracts_date_and_merchant_for_filename(self):
        fields = extract_receipt_fields(
            "별빛문구\n2026.11.21 14:20\n공책 1개 3,000원\n합계 3,000원"
        )

        self.assertEqual(fields["receipt_date"], "2026-11-21")
        self.assertEqual(fields["vendor"], "별빛문구")

    @patch("supabase_app.receipt_ocr.httpx.post")
    def test_sends_file_to_upstage_ocr(self, post):
        response = post.return_value
        response.json.return_value = {
            "text": "교과서 20개입\n12,000원 1개\n총 결제금액 12,000원"
        }

        with patch.dict(os.environ, {"UPSTAGE_API_KEY": "test-key"}):
            fields = parse_receipt(b"image-bytes", "receipt.jpg", "image/jpeg")

        self.assertEqual(fields["description"], "교과서 20개입")
        self.assertEqual(fields["amount"], 12000)
        self.assertEqual(fields["quantity"], 1)
        self.assertEqual(fields["items"][0]["amount"], 12000)
        self.assertEqual(
            post.call_args.args[0], "https://api.upstage.ai/v1/document-digitization"
        )


if __name__ == "__main__":
    unittest.main()
