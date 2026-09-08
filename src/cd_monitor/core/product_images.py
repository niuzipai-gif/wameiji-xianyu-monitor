"""Product-image normalization shared by capture, storage, and the board."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit


_NON_PRODUCT_IMAGE = re.compile(
    r"(?:searchlist|placeholder|paypaay|"
    r"(?:^|/)logo(?:[._/?#]|$)|"
    r"(?:^|/)sigmerchantimg/logo(?:[._/?#]|$)|"
    r"(?:^|/)(?:avatar|icon|qrcode|qr-code|sold)(?:[._/?#]|$))",
    re.IGNORECASE,
)


def normalize_product_image_url(value: object, *, base_url: str | None = None) -> str | None:
    """Return an absolute HTTP(S) image URL, including protocol-relative URLs."""

    url = str(value or "").strip()
    if not url or any(char.isspace() for char in url):
        return None
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        if not base_url:
            return None
        url = urljoin(base_url, url)
        parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def is_usable_product_image(value: object, *, base_url: str | None = None) -> bool:
    """Whether ``value`` is a real product-image candidate, not site chrome."""

    url = normalize_product_image_url(value, base_url=base_url)
    return bool(url and not _NON_PRODUCT_IMAGE.search(url))
