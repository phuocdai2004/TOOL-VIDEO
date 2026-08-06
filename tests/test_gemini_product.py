"""Unit tests for evidence-grounded Gemini product analysis."""

import json
import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.api.gemini_product import GeminiProductAnalyzer, GeminiProductError  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.ok = 200 <= status_code < 300

    def json(self) -> dict:
        return self._payload


def _success_payload() -> dict:
    analysis = {
        "product_name": "Bình giữ nhiệt KATO",
        "product_category": "Bình nước",
        "analysis_summary": "Ảnh cho thấy một bình nước màu đen có nắp.",
        "verified_facts": [
            {"fact": "Sản phẩm có màu đen", "source": "image", "evidence": "Thân bình trong ảnh có màu đen"},
            {"fact": "Sản phẩm có màu đen", "source": "image", "evidence": "Trùng lặp"},
            {"fact": "Giá 99.000 đồng", "source": "link", "evidence": ""},
        ],
        "suggested_audience": "Người thường mang nước khi đi làm",
        "hooks": ["Mang nước gọn hơn mỗi ngày"],
        "recommended_script": "Một thiết kế màu đen gọn gàng cho nhịp sống hằng ngày.",
        "scenes": [{"visual": "Cận cảnh sản phẩm", "voiceover": "Thiết kế màu đen gọn gàng."}],
        "missing_information": ["Dung tích", "Chất liệu"],
        "warnings": ["Không xác định được dung tích từ ảnh"],
    }
    return {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(analysis, ensure_ascii=False)}]}}
        ]
    }


class GeminiProductAnalyzerTests(unittest.TestCase):
    @patch("core.api.gemini_product.requests.post")
    def test_analyze_sends_inline_image_and_filters_unproven_facts(self, post):
        post.return_value = _FakeResponse(200, _success_payload())
        analyzer = GeminiProductAnalyzer("test-key", "gemini-3.6-flash")

        result = analyzer.analyze(
            b"image-bytes",
            "image/png",
            source_url="https://example.com/product",
            source_title="Bình KATO",
        )

        self.assertEqual(result.product_name, "Bình giữ nhiệt KATO")
        self.assertEqual(len(result.verified_facts), 1)
        self.assertEqual(result.verified_facts[0].source, "image")
        request = post.call_args.kwargs
        self.assertEqual(request["headers"]["x-goog-api-key"], "test-key")
        inline_data = request["json"]["contents"][0]["parts"][1]["inlineData"]
        self.assertEqual(inline_data["mimeType"], "image/png")
        self.assertEqual(inline_data["data"], "aW1hZ2UtYnl0ZXM=")
        self.assertNotIn("test-key", json.dumps(request["json"]))

    @patch("core.api.gemini_product.requests.post")
    def test_invalid_key_has_clear_error(self, post):
        post.return_value = _FakeResponse(401, {"error": {"message": "invalid"}})
        analyzer = GeminiProductAnalyzer("bad-key")

        with self.assertRaisesRegex(GeminiProductError, "API Key không hợp lệ"):
            analyzer.analyze(b"x", "image/jpeg")

    @patch("core.api.gemini_product.requests.post")
    def test_rate_limit_has_clear_error(self, post):
        post.return_value = _FakeResponse(429, {"error": {"message": "quota"}})
        analyzer = GeminiProductAnalyzer("test-key")

        with self.assertRaisesRegex(GeminiProductError, "hết hạn mức"):
            analyzer.analyze(b"x", "image/webp")

    def test_rejects_invalid_model_and_image_type(self):
        with self.assertRaisesRegex(GeminiProductError, "model Gemini"):
            GeminiProductAnalyzer("test-key", "../../bad-model")

        analyzer = GeminiProductAnalyzer("test-key")
        with self.assertRaisesRegex(GeminiProductError, "JPG, PNG, WEBP hoặc AVIF"):
            analyzer.analyze(b"x", "image/gif")


if __name__ == "__main__":
    unittest.main()
