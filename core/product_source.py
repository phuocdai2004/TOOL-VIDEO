"""Read public product pages and extract safe marketing metadata."""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import socket
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests


MAX_PRODUCT_HTML_BYTES = 2 * 1024 * 1024
MAX_PRODUCT_IMAGE_BYTES = 15 * 1024 * 1024
MAX_REDIRECTS = 4
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
)
SHOPEE_PREVIEW_USER_AGENT = (
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
)


class ProductSourceError(ValueError):
    """Raised when a product URL is invalid, unsafe, or unreadable."""


@dataclass(frozen=True)
class ProductSource:
    """Metadata discovered from a product page."""

    source_url: str
    title: str = ""
    description: str = ""
    image_url: str = ""
    site_name: str = ""

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable representation."""
        return asdict(self)


class _ProductHTMLParser(HTMLParser):
    """Collect metadata, images, title, and Product JSON-LD blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.images: list[str] = []
        self.title_parts: list[str] = []
        self.json_ld_blocks: list[str] = []
        self._in_title = False
        self._in_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        data = {str(key).lower(): value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            key = (data.get("property") or data.get("name") or "").strip().lower()
            content = data.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
            return
        if tag == "img":
            src = data.get("src") or data.get("data-src") or data.get("data-original")
            if src and not src.startswith("data:"):
                self.images.append(src.strip())
            return
        if tag == "script" and data.get("type", "").lower() == "application/ld+json":
            self._in_json_ld = True
            self._json_ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_json_ld:
            block = "".join(self._json_ld_parts).strip()
            if block:
                self.json_ld_blocks.append(block)
            self._in_json_ld = False
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_json_ld:
            self._json_ld_parts.append(data)


def _clean_text(value: Any, max_length: int) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length]


def _find_product_node(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            found = _find_product_node(item)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None

    node_type = value.get("@type", "")
    types = node_type if isinstance(node_type, list) else [node_type]
    if any(str(item).lower() == "product" for item in types):
        return value

    for key in ("@graph", "mainEntity", "itemListElement"):
        found = _find_product_node(value.get(key))
        if found:
            return found
    return None


def _json_ld_product(blocks: list[str]) -> dict[str, Any]:
    for block in blocks:
        try:
            parsed = json.loads(block)
        except (TypeError, ValueError):
            continue
        found = _find_product_node(parsed)
        if found:
            return found
    return {}


def _image_from_product_node(product: dict[str, Any]) -> str:
    image_value = product.get("image", "")
    if isinstance(image_value, list):
        image_value = image_value[0] if image_value else ""
    if isinstance(image_value, dict):
        image_value = image_value.get("url") or image_value.get("contentUrl") or ""
    return str(image_value or "")


def parse_product_html(page_html: str, base_url: str) -> ProductSource:
    """Extract product metadata from HTML without executing page scripts."""
    parser = _ProductHTMLParser()
    parser.feed(page_html)
    product = _json_ld_product(parser.json_ld_blocks)

    title = (
        product.get("name")
        or parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or " ".join(parser.title_parts)
    )
    description = (
        product.get("description")
        or parser.meta.get("og:description")
        or parser.meta.get("twitter:description")
        or parser.meta.get("description")
    )
    image_url = (
        _image_from_product_node(product)
        or parser.meta.get("og:image")
        or parser.meta.get("og:image:url")
        or parser.meta.get("twitter:image")
        or (parser.images[0] if parser.images else "")
    )
    site_name = parser.meta.get("og:site_name") or (urlparse(base_url).hostname or "")

    if image_url:
        image_url = urljoin(base_url, str(image_url).strip())
        if urlparse(image_url).scheme not in ("http", "https"):
            image_url = ""

    return ProductSource(
        source_url=base_url,
        title=_clean_text(title, 300),
        description=_clean_text(description, 4000),
        image_url=image_url,
        site_name=_clean_text(site_name, 120),
    )


def validate_product_url(url: str) -> str:
    """Validate that a URL targets a public HTTP(S) host."""
    raw_value = str(url or "").strip()
    if not raw_value or len(raw_value) > 8000:
        raise ProductSourceError("Link sản phẩm không hợp lệ")

    # Mobile shopping apps often copy a full share message instead of a bare
    # URL. Accept that text and extract the first public HTTP(S) link.
    url_match = re.search(r"https?://[^\s<>\"']+", raw_value, flags=re.IGNORECASE)
    normalized = (url_match.group(0) if url_match else raw_value).rstrip(
        "),.;!?]}>"
    )

    parsed = urlparse(normalized)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ProductSourceError("Link sản phẩm phải bắt đầu bằng http:// hoặc https://")
    if parsed.username or parsed.password:
        raise ProductSourceError("Link sản phẩm không được chứa thông tin đăng nhập")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ProductSourceError("Cổng trong link sản phẩm không hợp lệ") from exc
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ProductSourceError("Không tìm thấy máy chủ của link sản phẩm") from exc

    if not addresses:
        raise ProductSourceError("Không tìm thấy địa chỉ của link sản phẩm")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ProductSourceError("Link sản phẩm phải trỏ tới một trang công khai")
    return normalized


def _request_public_url(
    url: str,
    accept: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
) -> requests.Response:
    current_url = url
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": accept,
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        }
    )

    for _ in range(MAX_REDIRECTS + 1):
        current_url = validate_product_url(current_url)
        response = session.get(
            current_url,
            allow_redirects=False,
            stream=True,
            timeout=(8, 20),
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ProductSourceError("Link chuyển hướng không hợp lệ")
            current_url = urljoin(current_url, location)
            continue
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            response.close()
            raise ProductSourceError(f"Trang sản phẩm trả về lỗi HTTP {exc.response.status_code}") from exc
        response.url = current_url
        return response

    raise ProductSourceError("Link sản phẩm chuyển hướng quá nhiều lần")


def _read_limited(response: requests.Response, max_bytes: int) -> bytes:
    header_length = response.headers.get("Content-Length")
    if header_length and header_length.isdigit() and int(header_length) > max_bytes:
        response.close()
        raise ProductSourceError("Nội dung từ link sản phẩm quá lớn")

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            response.close()
            raise ProductSourceError("Nội dung từ link sản phẩm quá lớn")
        chunks.append(chunk)
    response.close()
    return b"".join(chunks)


def fetch_product_source(url: str) -> ProductSource:
    """Fetch a public product page and extract its primary metadata."""
    normalized_url = validate_product_url(url)
    hostname = (urlparse(normalized_url).hostname or "").lower()
    user_agent = (
        SHOPEE_PREVIEW_USER_AGENT
        if hostname == "shopee.vn" or hostname.endswith(".shopee.vn")
        else DEFAULT_USER_AGENT
    )
    response = _request_public_url(
        normalized_url,
        "text/html,application/xhtml+xml,image/avif,image/webp,image/*;q=0.8,*/*;q=0.5",
        user_agent=user_agent,
    )
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    final_url = response.url

    if content_type.startswith("image/"):
        response.close()
        return ProductSource(source_url=final_url, image_url=final_url)
    if content_type and content_type not in ("text/html", "application/xhtml+xml"):
        response.close()
        raise ProductSourceError("Link không trỏ tới trang hoặc ảnh sản phẩm")

    raw = _read_limited(response, MAX_PRODUCT_HTML_BYTES)
    encoding = response.encoding or "utf-8"
    try:
        page_html = raw.decode(encoding, errors="replace")
    except LookupError:
        page_html = raw.decode("utf-8", errors="replace")
    return parse_product_html(page_html, final_url)


def download_product_image(image_url: str, destination_stem: str) -> str:
    """Download a public product image with size and content-type limits."""
    content, content_type = fetch_product_image(image_url)
    extension = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/avif": ".avif",
        "image/gif": ".gif",
    }.get(content_type, ".img")
    os.makedirs(os.path.dirname(destination_stem), exist_ok=True)
    destination = destination_stem + extension
    with open(destination, "wb") as image_file:
        image_file.write(content)
    return destination


def fetch_product_image(image_url: str) -> tuple[bytes, str]:
    """Fetch a public product image into memory for multimodal analysis."""
    response = _request_public_url(image_url, "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.2")
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if not content_type.startswith("image/"):
        response.close()
        raise ProductSourceError("Ảnh đại diện từ link có định dạng không hợp lệ")
    content = _read_limited(response, MAX_PRODUCT_IMAGE_BYTES)
    return content, content_type
