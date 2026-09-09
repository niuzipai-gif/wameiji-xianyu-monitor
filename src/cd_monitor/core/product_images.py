"""Product-image normalization shared by capture, storage, and the board."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

_NON_PRODUCT_IMAGE = re.compile(
    r"(?:searchlist|placeholder|img_bg_wmj|headdefault|2-tps-2-2|paypaay|meyasu\.gif|"
    r"(?:img_)?no[-_]?image|65cca9650d72043406a3b17c5e36a619|"
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
    hostname = str(parsed.hostname or "").lower()
    if hostname == "imghk.doorzo.net" and parsed.path.startswith("/-/large/plain/"):
        # Doorzo rewrites Mercari Shops' public image host to its own Hong
        # Kong proxy.  That proxy fails TLS in ordinary Chrome on this host,
        # while the original Mercari CDN serves the identical path directly.
        return "https://assets.mercari-shops-static.com" + parsed.path + (
            f"?{parsed.query}" if parsed.query else ""
        )
    rakuten_proxy_prefix = "/tshopr10sjp/"
    if hostname.endswith(".doorzo.net") and parsed.path.startswith(rakuten_proxy_prefix):
        marketplace_path = parsed.path[len(rakuten_proxy_prefix) :].lstrip("/")
        if marketplace_path and "/cabinet/" in marketplace_path:
            return (
                "https://thumbnail.image.rakuten.co.jp/@0_mall/"
                + marketplace_path
                + "?_ex=600x600"
            )
    return url


def is_usable_product_image(value: object, *, base_url: str | None = None) -> bool:
    """Whether ``value`` is a real product-image candidate, not site chrome."""

    url = normalize_product_image_url(value, base_url=base_url)
    return bool(url and not _NON_PRODUCT_IMAGE.search(url))
