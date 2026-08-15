"""Focused tests for product-link metadata and product task state."""

import os
import socket
import sys
import unittest
from unittest.mock import patch

import requests


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.product_source import (  # noqa: E402
    ProductSourceError,
    fetch_product_source,
    parse_product_html,
    validate_product_url,
)
from models.task import ProductVideoTask, TaskType, parse_task_state  # noqa: E402


class ProductSourceParsingTests(unittest.TestCase):
    def test_prefers_product_json_ld(self) -> None:
        source = parse_product_html(
            """
            <html><head>
              <meta property="og:title" content="Fallback name">
              <script type="application/ld+json">
                {"@context":"https://schema.org","@type":"Product",
                 "name":"Áo khoác chống nắng","description":"Vải nhẹ, thoáng",
                 "image":["/images/coat.webp"]}
              </script>
            </head></html>
            """,
            "https://shop.example/items/coat",
        )

        self.assertEqual(source.title, "Áo khoác chống nắng")
        self.assertEqual(source.description, "Vải nhẹ, thoáng")
        self.assertEqual(source.image_url, "https://shop.example/images/coat.webp")

    def test_reads_open_graph_metadata(self) -> None:
        source = parse_product_html(
            """
            <meta property="og:title" content="Bình giữ nhiệt 900 ml">
            <meta property="og:description" content="Giữ nóng và lạnh">
            <meta property="og:image" content="https://cdn.example/bottle.jpg">
            <meta property="og:site_name" content="Example Shop">
            """,
            "https://shop.example/product/123",
        )

        self.assertEqual(source.title, "Bình giữ nhiệt 900 ml")
        self.assertEqual(source.site_name, "Example Shop")
        self.assertEqual(source.image_url, "https://cdn.example/bottle.jpg")

    def test_falls_back_to_first_relative_image(self) -> None:
        source = parse_product_html(
            '<html><head><title>Sản phẩm mẫu</title></head><body><img src="../main.png"></body></html>',
            "https://shop.example/products/item/",
        )

        self.assertEqual(source.title, "Sản phẩm mẫu")
        self.assertEqual(source.image_url, "https://shop.example/products/main.png")


class ProductUrlSafetyTests(unittest.TestCase):
    @patch("core.product_source.socket.getaddrinfo")
    def test_extracts_shopee_url_from_mobile_share_text(self, getaddrinfo) -> None:
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        ]

        normalized = validate_product_url(
            "Mời bạn xem sản phẩm này trên Shopee! "
            "https://s.shopee.vn/8AJexample?share_channel_code=1"
        )

        self.assertEqual(
            normalized,
            "https://s.shopee.vn/8AJexample?share_channel_code=1",
        )

    @patch("core.product_source.socket.getaddrinfo")
    def test_rejects_localhost_address(self, getaddrinfo) -> None:
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ]

        with self.assertRaises(ProductSourceError):
            validate_product_url("http://localhost/product")

    def test_rejects_invalid_port(self) -> None:
        with self.assertRaises(ProductSourceError):
            validate_product_url("https://example.com:not-a-port/item")


class ProductSourceFetchTests(unittest.TestCase):
    @patch("core.product_source.validate_product_url")
    @patch("core.product_source._request_public_url")
    def test_shopee_uses_public_preview_user_agent(self, request_public_url, validate_url) -> None:
        url = "https://shopee.vn/product/123/456"
        validate_url.side_effect = lambda value: value
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.encoding = "utf-8"
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response._content = b'''<meta property="og:title" content="Loa Bluetooth">
            <meta property="og:image" content="https://cdn.example/speaker.jpg">'''
        response.iter_content = lambda chunk_size: [response._content]
        response.close = lambda: None
        request_public_url.return_value = response

        source = fetch_product_source(url)

        self.assertEqual(source.title, "Loa Bluetooth")
        self.assertEqual(source.image_url, "https://cdn.example/speaker.jpg")
        self.assertIn(
            "facebookexternalhit",
            request_public_url.call_args.kwargs["user_agent"],
        )

    @patch("core.product_source.validate_product_url")
    @patch("core.product_source._request_public_url")
    def test_regular_sites_keep_browser_user_agent(self, request_public_url, validate_url) -> None:
        url = "https://shop.example/product/1"
        validate_url.side_effect = lambda value: value
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.encoding = "utf-8"
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response._content = b'<meta property="og:image" content="/product.jpg">'
        response.iter_content = lambda chunk_size: [response._content]
        response.close = lambda: None
        request_public_url.return_value = response

        source = fetch_product_source(url)

        self.assertEqual(source.image_url, "https://shop.example/product.jpg")
        self.assertIn("Mozilla/5.0", request_public_url.call_args.kwargs["user_agent"])


class ProductTaskStateTests(unittest.TestCase):
    def test_product_task_roundtrip(self) -> None:
        original = ProductVideoTask(
            task_id="product001",
            creative_name="Bình giữ nhiệt",
            product_name="Bình giữ nhiệt 900 ml",
            product_url="https://shop.example/item/1",
            reference_image="product.jpg",
        )

        restored = parse_task_state(original.model_dump())

        self.assertIsInstance(restored, ProductVideoTask)
        self.assertEqual(restored.task_type, TaskType.PRODUCT)
        self.assertEqual(restored.product_name, "Bình giữ nhiệt 900 ml")


if __name__ == "__main__":
    unittest.main()
