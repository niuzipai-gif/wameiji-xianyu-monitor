"""Publish a sanitized, image-complete dual-market snapshot for GitHub Pages."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests
from PIL import Image


ALLOWED_IMAGE_HOSTS = frozenset(
    {
        "img.alicdn.com",
        "auctions.c.yimg.jp",
        "thumbnail.image.rakuten.co.jp",
        "assets.mercari-shops-static.com",
        "static.mercdn.net",
        "img.fril.jp",
    }
)
IMAGE_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MIN_IMAGE_EDGE = 80
MAX_IMAGE_EDGE = 10_000
DEFAULT_DISPLAY_EXCHANGE_RATE_CNY_PER_JPY = 0.046
PUBLIC_CATEGORY = "eligible"
_POLICY_MINIMUM_MARGINS = {
    "wameiji-xianyu-net-v1": 0.25,
    "wameiji-xianyu-net-v2": 0.0,
}
SOURCE_FIELDS = (
    "listing_id",
    "source",
    "canonical_product_key",
    "title",
    "price",
    "currency",
    "url",
    "availability",
    "condition_group",
    "completeness",
    "evidence_level",
    "captured_at",
)
CALCULATION_FIELDS = (
    "status",
    "sale_price_cny",
    "landed_cost_cny",
    "expected_profit_cny",
    "net_margin",
    "created_at",
    "cost_breakdown",
)
COST_BREAKDOWN_FIELDS = (
    "policy_version",
    "minimum_net_margin",
    "missing_fields",
    "xianyu_sale_cny",
    "xianyu_seller_fee_cny",
    "wameiji_exchange_rate_cny_per_jpy",
    "wameiji_item_jpy",
    "wameiji_item_cny",
    "wameiji_domestic_shipping_jpy",
    "wameiji_domestic_shipping_cny",
    "wameiji_proxy_fee_jpy",
    "wameiji_proxy_fee_cny",
    "wameiji_purchase_cny",
    "international_shipping_cny",
    "china_postage_cny",
    "packaging_cny",
    "after_sale_reserve_cny",
    "risk_reserve_cny",
    "tax_cny",
    "landed_cost_cny",
    "net_profit_cny",
    "net_margin",
)


class SnapshotExportError(RuntimeError):
    """Raised when no safe, complete Pages snapshot can be published."""


@dataclass(frozen=True)
class DownloadedImage:
    body: bytes
    content_type: str


@dataclass(frozen=True)
class SnapshotExportResult:
    snapshot_path: Path
    manifest_path: Path
    asset_paths: tuple[Path, ...]
    dropped_comparison_ids: tuple[int, ...]


def _parsed_https_url(value: object) -> object:
    parsed = urlparse(str(value or "").strip())
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise SnapshotExportError("source URL must be public HTTPS without credentials")
    return parsed


def download_public_image(url: str) -> DownloadedImage:
    """Download one image from an observed marketplace CDN we publish."""

    parsed = _parsed_https_url(url)
    if parsed.hostname.casefold() not in ALLOWED_IMAGE_HOSTS:
        raise SnapshotExportError(f"image host is not allowed: {parsed.hostname}")

    with requests.get(
        url,
        timeout=(5, 20),
        headers={"User-Agent": "WAMEIJI-XIANYU snapshot exporter/1"},
        allow_redirects=False,
        stream=True,
    ) as response:
        response.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise SnapshotExportError("image exceeds the publish size limit")
            chunks.append(chunk)
        return DownloadedImage(
            body=b"".join(chunks),
            content_type=response.headers.get("Content-Type", ""),
        )


def _verified_image(download: DownloadedImage) -> tuple[bytes, str, int, int]:
    if not download.content_type.casefold().startswith("image/") or len(download.body) < 256:
        raise SnapshotExportError("response is not a usable image")
    if len(download.body) > MAX_IMAGE_BYTES:
        raise SnapshotExportError("image exceeds the publish size limit")

    try:
        with Image.open(io.BytesIO(download.body)) as image:
            image.verify()
        with Image.open(io.BytesIO(download.body)) as image:
            image.load()
            width, height = image.size
            image_format = str(image.format or "").upper()
    except (OSError, SyntaxError, ValueError) as exc:
        raise SnapshotExportError("image cannot be decoded") from exc

    if (
        image_format not in IMAGE_EXTENSIONS
        or min(width, height) < MIN_IMAGE_EDGE
        or max(width, height) > MAX_IMAGE_EDGE
    ):
        raise SnapshotExportError("image dimensions or format are not publishable")
    return download.body, IMAGE_EXTENSIONS[image_format], width, height


def _finite_price(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def _public_source(
    raw_source: object,
    *,
    expected_source: str,
    expected_evidence: str,
    canonical_key: str,
) -> tuple[dict[str, object], str]:
    if not isinstance(raw_source, dict):
        raise SnapshotExportError(f"comparison is missing {expected_source}")
    if raw_source.get("source") != expected_source:
        raise SnapshotExportError(f"comparison source is not {expected_source}")
    if raw_source.get("evidence_level") != expected_evidence:
        raise SnapshotExportError(f"{expected_source} evidence level is invalid")
    if str(raw_source.get("canonical_product_key") or "").strip() != canonical_key:
        raise SnapshotExportError(f"{expected_source} canonical key does not match")
    if not str(raw_source.get("title") or "").strip():
        raise SnapshotExportError(f"{expected_source} title is missing")
    if not _finite_price(raw_source.get("price")):
        raise SnapshotExportError(f"{expected_source} price is invalid")
    if raw_source.get("currency") != ("CNY" if expected_source == "xianyu" else "JPY"):
        raise SnapshotExportError(f"{expected_source} currency is invalid")
    _parsed_https_url(raw_source.get("url"))
    image_url = str(raw_source.get("image_url") or "").strip()
    _parsed_https_url(image_url)
    if not str(raw_source.get("captured_at") or "").strip():
        raise SnapshotExportError(f"{expected_source} capture time is missing")
    return ({field: raw_source.get(field) for field in SOURCE_FIELDS}, image_url)


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _public_card(
    raw_card: object,
    *,
    minimum_net_margin: float,
    policy_version: str,
) -> tuple[int, dict[str, object], dict[str, str]]:
    if not isinstance(raw_card, dict):
        raise SnapshotExportError("comparison is not an object")
    comparison_id = raw_card.get("comparison_id")
    canonical_key = str(raw_card.get("canonical_product_key") or "").strip()
    if isinstance(comparison_id, bool) or not isinstance(comparison_id, int) or comparison_id <= 0:
        raise SnapshotExportError("comparison id is invalid")
    if not canonical_key:
        raise SnapshotExportError("comparison canonical key is missing")

    xianyu, xianyu_image = _public_source(
        raw_card.get("xianyu"),
        expected_source="xianyu",
        expected_evidence="search_card",
        canonical_key=canonical_key,
    )
    wameiji, wameiji_image = _public_source(
        raw_card.get("wameiji"),
        expected_source="wameiji",
        expected_evidence="detail_verified",
        canonical_key=canonical_key,
    )
    raw_calculation = raw_card.get("calculation")
    if not isinstance(raw_calculation, dict):
        raise SnapshotExportError("comparison calculation is missing")
    if raw_calculation.get("status") != PUBLIC_CATEGORY:
        raise SnapshotExportError("comparison calculation is not eligible")
    if not _finite_number(raw_calculation.get("expected_profit_cny")) or float(
        raw_calculation["expected_profit_cny"]
    ) <= 0:
        raise SnapshotExportError("eligible comparison profit is invalid")
    if not _finite_number(raw_calculation.get("net_margin")) or float(
        raw_calculation["net_margin"]
    ) < minimum_net_margin:
        raise SnapshotExportError("eligible comparison is below the margin threshold")
    raw_breakdown = raw_calculation.get("cost_breakdown")
    if not isinstance(raw_breakdown, dict):
        raise SnapshotExportError("eligible comparison cost breakdown is missing")
    if raw_breakdown.get("policy_version") != policy_version:
        raise SnapshotExportError("eligible comparison policy version does not match")
    if raw_breakdown.get("missing_fields") != []:
        raise SnapshotExportError("eligible comparison has missing cost fields")

    required_breakdown_numbers = set(COST_BREAKDOWN_FIELDS) - {
        "policy_version",
        "missing_fields",
    }
    if any(not _finite_number(raw_breakdown.get(name)) for name in required_breakdown_numbers):
        raise SnapshotExportError("eligible comparison cost breakdown is incomplete")
    if float(raw_breakdown["net_margin"]) < minimum_net_margin:
        raise SnapshotExportError("eligible cost breakdown is below the margin threshold")

    calculation = {field: raw_calculation.get(field) for field in CALCULATION_FIELDS}
    calculation["cost_breakdown"] = {
        field: raw_breakdown.get(field) for field in COST_BREAKDOWN_FIELDS
    }

    return (
        comparison_id,
        {
            "comparison_id": comparison_id,
            "canonical_product_key": canonical_key,
            "xianyu": xianyu,
            "wameiji": wameiji,
            "calculation": calculation,
        },
        {"xianyu": xianyu_image, "wameiji": wameiji_image},
    )


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def export_pages_snapshot(
    board: dict[str, object],
    web_dir: Path,
    *,
    generated_at: datetime,
    fetch_image: Callable[[str], DownloadedImage] = download_public_image,
) -> SnapshotExportResult:
    """Export only complete, exact two-sided comparisons and their images."""

    if generated_at.utcoffset() is None:
        raise SnapshotExportError("generated_at must include a timezone")
    if not isinstance(board, dict):
        raise SnapshotExportError("board must be an object")
    strategy = board.get("strategy")
    if not isinstance(strategy, dict):
        raise SnapshotExportError("board strategy is missing")
    if strategy.get("trade_direction") != "wameiji_jpy_to_xianyu_cny":
        raise SnapshotExportError("board trade direction is invalid")
    policy_version = str(strategy.get("policy_version") or "").strip()
    minimum_net_margin = strategy.get("minimum_net_margin")
    if not policy_version or not _finite_number(minimum_net_margin):
        raise SnapshotExportError("board profit policy is incomplete")
    minimum_net_margin = float(minimum_net_margin)
    required_minimum_margin = _POLICY_MINIMUM_MARGINS.get(policy_version)
    if required_minimum_margin is None or minimum_net_margin < required_minimum_margin:
        raise SnapshotExportError("board profit policy is unsupported")
    raw_summary = board.get("summary")
    if not isinstance(raw_summary, dict):
        raise SnapshotExportError("board summary is missing")
    summary_fields = (
        "evaluated_count",
        "eligible_count",
        "below_margin_count",
        "cost_pending_count",
        "waiting_wameiji_count",
        "waiting_xianyu_count",
    )
    if any(
        isinstance(raw_summary.get(field), bool)
        or not isinstance(raw_summary.get(field), int)
        or int(raw_summary[field]) < 0
        for field in summary_fields
    ):
        raise SnapshotExportError("board summary counts are invalid")
    source_rows = board.get(PUBLIC_CATEGORY, [])
    if not isinstance(source_rows, list):
        raise SnapshotExportError("board eligible category is not a list")
    if int(raw_summary["eligible_count"]) != len(source_rows):
        raise SnapshotExportError("board eligible count does not match its cards")

    snapshot_id = generated_at.strftime("%Y%m%dT%H%M%S%z")
    web_dir = Path(web_dir)
    assets_root = web_dir / "assets" / "dual-market"
    assets_root.mkdir(parents=True, exist_ok=True)
    final_asset_dir = assets_root / snapshot_id
    if final_asset_dir.exists():
        raise SnapshotExportError(f"snapshot asset directory already exists: {snapshot_id}")
    staging = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}-", dir=assets_root))

    accepted: list[dict[str, object]] = []
    dropped: list[int] = []
    manifest_assets: list[dict[str, object]] = []
    seen_comparison_ids: set[int] = set()
    try:
        for raw_card in source_rows:
                card_files: list[Path] = []
                comparison_id = (
                    raw_card.get("comparison_id") if isinstance(raw_card, dict) else None
                )
                try:
                    checked_id, card, source_images = _public_card(
                        raw_card,
                        minimum_net_margin=minimum_net_margin,
                        policy_version=policy_version,
                    )
                    if checked_id in seen_comparison_ids:
                        raise SnapshotExportError("comparison id is duplicated")
                    card_assets: list[dict[str, object]] = []
                    for source_name in ("xianyu", "wameiji"):
                        body, extension, width, height = _verified_image(
                            fetch_image(source_images[source_name])
                        )
                        filename = f"{checked_id}-{source_name}{extension}"
                        staged_file = staging / filename
                        staged_file.write_bytes(body)
                        card_files.append(staged_file)
                        relative = (
                            Path("assets") / "dual-market" / snapshot_id / filename
                        ).as_posix()
                        card[source_name]["image_url"] = relative
                        card_assets.append(
                            {
                                "path": relative,
                                "source": source_name,
                                "comparison_id": checked_id,
                                "sha256": hashlib.sha256(body).hexdigest(),
                                "width": width,
                                "height": height,
                            }
                        )
                    accepted.append(card)
                    manifest_assets.extend(card_assets)
                    seen_comparison_ids.add(checked_id)
                except (
                    KeyError,
                    OSError,
                    requests.RequestException,
                    SnapshotExportError,
                    ValueError,
                ):
                    for staged_file in card_files:
                        staged_file.unlink(missing_ok=True)
                    if isinstance(comparison_id, int) and not isinstance(comparison_id, bool):
                        dropped.append(comparison_id)

        published_count = len(accepted)
        if source_rows and published_count == 0:
            raise SnapshotExportError(
                "no publishable comparisons; no publishable eligible comparisons"
            )

        os.replace(staging, final_asset_dir)
        public_summary = {field: int(raw_summary[field]) for field in summary_fields}
        if dropped:
            public_summary["source_eligible_count"] = public_summary["eligible_count"]
            public_summary["eligible_count"] = published_count
        payload: dict[str, object] = {
            "schema_version": 2,
            "generated_at": generated_at.isoformat(),
            "source_board_generated_at": board.get("generated_at"),
            "mode": "verified_static_snapshot",
            "display_exchange_rate_cny_per_jpy": (
                float(board["display_exchange_rate_cny_per_jpy"])
                if _finite_price(board.get("display_exchange_rate_cny_per_jpy"))
                else DEFAULT_DISPLAY_EXCHANGE_RATE_CNY_PER_JPY
            ),
            "strategy": {
                "policy_version": policy_version,
                "trade_direction": strategy["trade_direction"],
                "minimum_net_margin": minimum_net_margin,
                "margin_denominator": strategy.get("margin_denominator"),
            },
            "summary": public_summary,
            "eligible": accepted,
            "below_margin": [],
            "cost_pending": [],
            "waiting_wameiji": [],
            "waiting_xianyu": [],
            "collector": {"state": "paused"},
        }
        manifest: dict[str, object] = {
            "schema_version": 2,
            "snapshot_id": snapshot_id,
            "generated_at": generated_at.isoformat(),
            "published_comparisons": published_count,
            "source_eligible_comparisons": len(source_rows),
            "policy_version": policy_version,
            "dropped_comparison_ids": sorted(set(dropped)),
            "assets": manifest_assets,
        }
        data_dir = web_dir / "data"
        manifest_path = data_dir / "dual-market-snapshot.manifest.json"
        snapshot_path = data_dir / "dual-market-snapshot.json"
        _atomic_json(manifest_path, manifest)
        _atomic_json(snapshot_path, payload)
        return SnapshotExportResult(
            snapshot_path=snapshot_path,
            manifest_path=manifest_path,
            asset_paths=tuple(web_dir / str(item["path"]) for item in manifest_assets),
            dropped_comparison_ids=tuple(sorted(set(dropped))),
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging)
