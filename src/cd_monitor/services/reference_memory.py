"""Local import and persistence for approved reference-product evidence.

All functions in this module are intentionally local-only. Screenshot import
does not make network requests, and candidate matching never changes normal
discovery eligibility or price policy.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from cd_monitor.core.reference_memory import (
    CandidateEvidence,
    ReferenceMatch,
    ReferenceProductEvidence,
    build_candidate_evidence,
    normalize_reference_tokens,
    score_candidate_against_product,
)
from cd_monitor.storage.sqlite import init_db

_CHECKSUM_RE = re.compile(r"[0-9a-f]{64}")
_SUPPORTED_IMAGE_SUFFIXES = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".webp"})
_MAX_EXTRACTED_TEXT_CHARS = 16_000
_TESSERACT_DEFAULT_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
_BARCODE_TYPES = frozenset({"EAN13", "EAN8", "UPCA"})
_OBSERVATION_MARKETS = frozenset({"wameiji", "xianyu"})
_OBSERVATION_STATES = frozenset(
    {"found", "price_unfavorable", "not_currently_listed", "login_required", "blocked"}
)
_SENSITIVE_URL_QUERY_KEYS = frozenset({"access_token", "api_key", "authorization", "cookie", "token"})


@dataclass(frozen=True)
class ExtractedReferenceSample:
    """Local, auditable features distilled from one approved screenshot."""

    checksum_sha256: str
    barcode: str | None
    extracted_text: str
    extraction_state: str
    ocr_languages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        checksum = self.checksum_sha256.casefold()
        if not _CHECKSUM_RE.fullmatch(checksum):
            raise ValueError("checksum_sha256 must be a lowercase SHA-256 hex digest")
        if self.barcode is not None and not _is_ean_jan(self.barcode):
            raise ValueError("barcode must be an 8, 12, or 13 digit EAN/JAN value")
        if not isinstance(self.extracted_text, str):
            raise TypeError("extracted_text must be text")
        if not isinstance(self.extraction_state, str) or not self.extraction_state.strip():
            raise ValueError("extraction_state must be non-empty text")
        if not all(isinstance(language, str) for language in self.ocr_languages):
            raise TypeError("ocr_languages must contain text values")
        object.__setattr__(self, "checksum_sha256", checksum)
        object.__setattr__(self, "extracted_text", self.extracted_text[:_MAX_EXTRACTED_TEXT_CHARS])
        object.__setattr__(self, "ocr_languages", tuple(sorted(set(self.ocr_languages))))


@dataclass(frozen=True)
class ReferenceImportReport:
    input_images: int
    imported_samples: int
    existing_samples: int
    refreshed_samples: int
    product_count: int
    barcode_sample_count: int
    ocr_languages: tuple[str, ...]
    network_requests: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "input_images": self.input_images,
            "imported_samples": self.imported_samples,
            "existing_samples": self.existing_samples,
            "refreshed_samples": self.refreshed_samples,
            "product_count": self.product_count,
            "barcode_sample_count": self.barcode_sample_count,
            "ocr_languages": list(self.ocr_languages),
            "network_requests": self.network_requests,
        }


class ReferenceSampleExtractor(Protocol):
    ocr_languages: tuple[str, ...]

    def extract(self, path: Path) -> ExtractedReferenceSample: ...


class LocalReferenceSampleExtractor:
    """Best-effort local-only image extractor with optional OCR and barcode read."""

    def __init__(
        self,
        *,
        tesseract_executable: Path | None = None,
        tessdata_dir: Path | None = None,
    ) -> None:
        self._tesseract_executable = tesseract_executable or _find_tesseract()
        self._tessdata_dir = tessdata_dir
        self.ocr_languages = _available_tesseract_languages(
            self._tesseract_executable,
            tessdata_dir=self._tessdata_dir,
        )

    def extract(self, path: Path) -> ExtractedReferenceSample:
        checksum = _file_checksum(path)
        barcode, barcode_state = _decode_barcode(path)
        extracted_text, ocr_state = _extract_ocr_text(
            path,
            executable=self._tesseract_executable,
            languages=self.ocr_languages,
            tessdata_dir=self._tessdata_dir,
        )
        return ExtractedReferenceSample(
            checksum_sha256=checksum,
            barcode=barcode,
            extracted_text=extracted_text,
            extraction_state=f"{barcode_state};{ocr_state}",
            ocr_languages=self.ocr_languages,
        )


def import_reference_samples(
    db_path: str | Path,
    folder: str | Path,
    *,
    extractor: ReferenceSampleExtractor | None = None,
    refresh_existing: bool = False,
) -> ReferenceImportReport:
    """Import local positive screenshot evidence idempotently.

    Exact EAN/JAN is the only product-grouping key. Unbarcoded screenshots stay
    separate by checksum even if their OCR text happens to be identical.
    """

    root = Path(folder)
    if not root.is_dir():
        raise ValueError(f"reference folder is not a directory: {root}")
    paths = _list_reference_images(root)
    active_extractor = extractor or LocalReferenceSampleExtractor()
    init_db(db_path)

    imported_samples = 0
    existing_samples = 0
    refreshed_samples = 0
    with sqlite3.connect(db_path) as conn:
        for path in paths:
            # The normal idempotent path only needs the local checksum.  Avoid
            # invoking OCR/barcode decoding again for a byte-identical sample;
            # an explicit refresh deliberately bypasses this fast path.
            if not refresh_existing:
                known_checksum = _file_checksum(path)
                known = conn.execute(
                    "SELECT 1 FROM reference_product_samples WHERE checksum_sha256 = ?",
                    (known_checksum,),
                ).fetchone()
                if known is not None:
                    existing_samples += 1
                    continue

            sample = active_extractor.extract(path)
            exists = conn.execute(
                "SELECT product_id FROM reference_product_samples WHERE checksum_sha256 = ?",
                (sample.checksum_sha256,),
            ).fetchone()
            if exists is not None:
                existing_samples += 1
                if refresh_existing:
                    tokens = sorted(normalize_reference_tokens(sample.extracted_text))
                    conn.execute(
                        """
                        UPDATE reference_product_samples
                        SET source_path = ?, extracted_text = ?, tokens_json = ?,
                            extraction_state = ?, ocr_languages_json = ?
                        WHERE checksum_sha256 = ?
                        """,
                        (
                            str(path.resolve()),
                            sample.extracted_text,
                            json.dumps(tokens, ensure_ascii=False, separators=(",", ":")),
                            sample.extraction_state,
                            json.dumps(
                                sample.ocr_languages,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            sample.checksum_sha256,
                        ),
                    )
                    refreshed_samples += 1
                continue

            product_id = _upsert_reference_product(conn, sample)
            tokens = sorted(normalize_reference_tokens(sample.extracted_text))
            conn.execute(
                """
                INSERT INTO reference_product_samples (
                  checksum_sha256, product_id, source_path, barcode, extracted_text,
                  tokens_json, extraction_state, ocr_languages_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample.checksum_sha256,
                    product_id,
                    str(path.resolve()),
                    sample.barcode,
                    sample.extracted_text,
                    json.dumps(tokens, ensure_ascii=False, separators=(",", ":")),
                    sample.extraction_state,
                    json.dumps(sample.ocr_languages, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            imported_samples += 1

        product_count = int(
            conn.execute("SELECT COUNT(*) FROM reference_products").fetchone()[0]
        )
        barcode_sample_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM reference_product_samples WHERE barcode IS NOT NULL"
            ).fetchone()[0]
        )

    languages = tuple(getattr(active_extractor, "ocr_languages", ()))
    return ReferenceImportReport(
        input_images=len(paths),
        imported_samples=imported_samples,
        existing_samples=existing_samples,
        refreshed_samples=refreshed_samples,
        product_count=product_count,
        barcode_sample_count=barcode_sample_count,
        ocr_languages=tuple(sorted(set(languages))),
    )


def refresh_discovery_candidate_reference_match(
    db_path: str | Path, candidate_id: int
) -> ReferenceMatch:
    """Persist a best-effort positive-reference match for an existing candidate."""

    if type(candidate_id) is not int or candidate_id <= 0:
        raise ValueError("candidate_id must be a positive integer")
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, title, artist, edition, catalog_no, jan, raw_text
            FROM discovery_candidates
            WHERE id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"discovery candidate does not exist: {candidate_id}")
        candidate = build_candidate_evidence(
            title=row["title"],
            artist=row["artist"],
            edition=row["edition"],
            catalog_no=row["catalog_no"],
            jan=row["jan"],
            raw_text=row["raw_text"],
        )
        match = _best_reference_match(conn, candidate)
        conn.execute(
            """
            INSERT INTO reference_candidate_matches (
              candidate_id, reference_product_id, score, match_kind, evidence_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(candidate_id) DO UPDATE SET
              reference_product_id = excluded.reference_product_id,
              score = excluded.score,
              match_kind = excluded.match_kind,
              evidence_json = excluded.evidence_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                candidate_id,
                match.product_id,
                match.score,
                match.match_kind,
                json.dumps(match.evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )
    return match


def read_candidate_reference_match(
    db_path: str | Path, candidate_id: int
) -> dict[str, object] | None:
    """Read one persisted match without mutating candidate state."""

    if type(candidate_id) is not int or candidate_id <= 0:
        raise ValueError("candidate_id must be a positive integer")
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT candidate_id, reference_product_id, score, match_kind, evidence_json
            FROM reference_candidate_matches
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()
    if row is None:
        return None
    evidence = json.loads(row[4])
    if type(evidence) is not dict:
        raise ValueError("persisted reference evidence is malformed")
    return {
        "candidate_id": int(row[0]),
        "reference_product_id": int(row[1]) if row[1] is not None else None,
        "score": float(row[2]),
        "match_kind": str(row[3]),
        "evidence": evidence,
    }


def record_reference_market_observation(
    db_path: str | Path,
    *,
    product_id: int,
    market: str,
    observation_state: str,
    observed_title: str | None = None,
    version_evidence: str | None = None,
    catalog_no: str | None = None,
    barcode: str | None = None,
    price: float | None = None,
    currency: str | None = None,
    source_url: str | None = None,
    note: str | None = None,
    observed_at: datetime | str | None = None,
) -> dict[str, object]:
    """Persist one volatile market observation without changing product labels.

    ``price_unfavorable`` and ``not_currently_listed`` are market states only:
    no row in ``reference_products`` or ``reference_candidate_matches`` is
    modified by this function.
    """

    _require_positive_int(product_id, "product_id")
    normalized_market = _normalize_choice(market, _OBSERVATION_MARKETS, "market")
    normalized_state = _normalize_choice(
        observation_state, _OBSERVATION_STATES, "observation_state"
    )
    normalized_barcode = _clean_barcode(barcode) if barcode is not None else None
    if barcode is not None and normalized_barcode is None:
        raise ValueError("barcode must be an 8, 12, or 13 digit EAN/JAN value")
    normalized_price = _normalize_optional_price(price)
    normalized_currency = _normalize_optional_currency(currency)
    if normalized_price is not None and normalized_currency is None:
        raise ValueError("currency is required when price is recorded")
    values = {
        "observed_title": _optional_observation_text(observed_title, "observed_title"),
        "version_evidence": _optional_observation_text(version_evidence, "version_evidence"),
        "catalog_no": _optional_observation_text(catalog_no, "catalog_no"),
        "note": _optional_observation_text(note, "note"),
    }
    normalized_url = _normalize_observation_url(source_url)
    observed_timestamp = _normalize_observed_at(observed_at)

    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        product = conn.execute(
            "SELECT 1 FROM reference_products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            raise ValueError(f"reference product does not exist: {product_id}")
        cursor = conn.execute(
            """
            INSERT INTO reference_market_observations (
              reference_product_id, market, observation_state, observed_at,
              observed_title, version_evidence, catalog_no, barcode, price,
              currency, source_url, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                normalized_market,
                normalized_state,
                observed_timestamp,
                values["observed_title"],
                values["version_evidence"],
                values["catalog_no"],
                normalized_barcode,
                normalized_price,
                normalized_currency,
                normalized_url,
                values["note"],
            ),
        )
        row = conn.execute(
            """
            SELECT id, reference_product_id, market, observation_state, observed_at,
                   observed_title, version_evidence, catalog_no, barcode, price,
                   currency, source_url, note
            FROM reference_market_observations
            WHERE id = ?
            """,
            (int(cursor.lastrowid),),
        ).fetchone()
    assert row is not None
    return _market_observation_dict(row)


def list_reference_market_observations(
    db_path: str | Path, *, product_id: int | None = None, limit: int = 100
) -> list[dict[str, object]]:
    """Return persisted observations only; this function never opens a browser."""

    if product_id is not None:
        _require_positive_int(product_id, "product_id")
    if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer from 1 to 200")
    init_db(db_path)
    where = "WHERE reference_product_id = ?" if product_id is not None else ""
    params: tuple[object, ...] = (product_id, limit) if product_id is not None else (limit,)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, reference_product_id, market, observation_state, observed_at,
                   observed_title, version_evidence, catalog_no, barcode, price,
                   currency, source_url, note
            FROM reference_market_observations
            {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_market_observation_dict(row) for row in rows]


def reference_memory_status(db_path: str | Path) -> dict[str, object]:
    """Return local coverage counts without reading files or the network."""

    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        product_count = int(conn.execute("SELECT COUNT(*) FROM reference_products").fetchone()[0])
        sample_count = int(
            conn.execute("SELECT COUNT(*) FROM reference_product_samples").fetchone()[0]
        )
        barcode_sample_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM reference_product_samples WHERE barcode IS NOT NULL"
            ).fetchone()[0]
        )
        matched_candidate_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM reference_candidate_matches WHERE reference_product_id IS NOT NULL"
            ).fetchone()[0]
        )
        market_observation_count = int(
            conn.execute("SELECT COUNT(*) FROM reference_market_observations").fetchone()[0]
        )
        state_rows = conn.execute(
            """
            SELECT observation_state, COUNT(*)
            FROM reference_market_observations
            GROUP BY observation_state
            ORDER BY observation_state
            """
        ).fetchall()
    return {
        "product_count": product_count,
        "sample_count": sample_count,
        "barcode_sample_count": barcode_sample_count,
        "matched_candidate_count": matched_candidate_count,
        "market_observation_count": market_observation_count,
        "market_observation_states": {str(state): int(count) for state, count in state_rows},
        "network_requests": 0,
    }


def list_reference_research_queue(
    db_path: str | Path, *, limit: int = 200
) -> list[dict[str, object]]:
    """List local identity clues used to drive externally authorized research.

    It returns no live facts and does not interact with a browser. A product
    whose market observation is absent or stale remains eligible for a future
    manual, tab-bounded check.
    """

    if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer from 1 to 200")
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.stable_key, p.barcode, p.tokens_json, p.sample_count,
                   (
                     SELECT s.source_path FROM reference_product_samples AS s
                     WHERE s.product_id = p.id ORDER BY s.id LIMIT 1
                   ) AS source_path,
                   (
                     SELECT s.extracted_text FROM reference_product_samples AS s
                     WHERE s.product_id = p.id ORDER BY s.id LIMIT 1
                   ) AS extracted_text
            FROM reference_products AS p
            ORDER BY p.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "product_id": int(row[0]),
            "stable_key": str(row[1]),
            "barcode": str(row[2]) if row[2] is not None else None,
            "tokens": sorted(_tokens_from_json(row[3])),
            "sample_count": int(row[4]),
            "source_path": str(row[5]) if row[5] is not None else None,
            "extracted_text": str(row[6] or ""),
        }
        for row in rows
    ]


def list_reference_candidate_matches(
    db_path: str | Path, *, limit: int = 100
) -> list[dict[str, object]]:
    """List persisted candidate evidence without performing another match."""

    if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer from 1 to 200")
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT m.candidate_id, m.reference_product_id, m.score, m.match_kind,
                   m.evidence_json, m.updated_at, c.title, p.stable_key, p.barcode
            FROM reference_candidate_matches AS m
            LEFT JOIN discovery_candidates AS c ON c.id = m.candidate_id
            LEFT JOIN reference_products AS p ON p.id = m.reference_product_id
            ORDER BY m.updated_at DESC, m.candidate_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    results: list[dict[str, object]] = []
    for row in rows:
        evidence = json.loads(row[4])
        if type(evidence) is not dict:
            raise ValueError("persisted reference evidence is malformed")
        results.append(
            {
                "candidate_id": int(row[0]),
                "reference_product_id": int(row[1]) if row[1] is not None else None,
                "score": float(row[2]),
                "match_kind": str(row[3]),
                "evidence": evidence,
                "updated_at": str(row[5]),
                "candidate_title": str(row[6]) if row[6] is not None else None,
                "reference_stable_key": str(row[7]) if row[7] is not None else None,
                "reference_barcode": str(row[8]) if row[8] is not None else None,
            }
        )
    return results


def _upsert_reference_product(conn: sqlite3.Connection, sample: ExtractedReferenceSample) -> int:
    stable_key = f"jan:{sample.barcode}" if sample.barcode else f"sample:{sample.checksum_sha256}"
    new_tokens = normalize_reference_tokens(sample.extracted_text)
    row = conn.execute(
        "SELECT id, tokens_json FROM reference_products WHERE stable_key = ?",
        (stable_key,),
    ).fetchone()
    if row is None:
        cursor = conn.execute(
            """
            INSERT INTO reference_products (stable_key, barcode, tokens_json, sample_count)
            VALUES (?, ?, ?, 1)
            """,
            (
                stable_key,
                sample.barcode,
                json.dumps(sorted(new_tokens), ensure_ascii=False, separators=(",", ":")),
            ),
        )
        return int(cursor.lastrowid)

    product_id = int(row[0])
    merged_tokens = _tokens_from_json(row[1]) | new_tokens
    conn.execute(
        """
        UPDATE reference_products
        SET tokens_json = ?, sample_count = sample_count + 1, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            json.dumps(sorted(merged_tokens), ensure_ascii=False, separators=(",", ":")),
            product_id,
        ),
    )
    return product_id


def _best_reference_match(conn: sqlite3.Connection, candidate: CandidateEvidence) -> ReferenceMatch:
    rows = conn.execute(
        "SELECT id, stable_key, barcode, tokens_json FROM reference_products ORDER BY id"
    ).fetchall()
    matched: list[ReferenceMatch] = []
    for row in rows:
        product = ReferenceProductEvidence(
            product_id=int(row[0]),
            stable_key=str(row[1]),
            barcode=str(row[2]) if row[2] is not None else None,
            tokens=_tokens_from_json(row[3]),
        )
        result = score_candidate_against_product(candidate, product)
        if result.product_id is not None:
            matched.append(result)
    if not matched:
        return ReferenceMatch(
            product_id=None,
            score=0.0,
            match_kind="no_match",
            evidence={"shared_tokens": []},
        )
    return max(
        matched,
        key=lambda result: (
            result.score,
            1 if result.match_kind == "exact_barcode" else 0,
            -(result.product_id or 0),
        ),
    )


def _list_reference_images(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.casefold() in _SUPPORTED_IMAGE_SUFFIXES
    )


def _tokens_from_json(value: object) -> frozenset[str]:
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("persisted reference tokens are malformed") from exc
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise ValueError("persisted reference tokens must be a text list")
    return normalize_reference_tokens(frozenset(decoded))


def _market_observation_dict(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    return {
        "id": int(row[0]),
        "product_id": int(row[1]),
        "market": str(row[2]),
        "observation_state": str(row[3]),
        "observed_at": str(row[4]),
        "observed_title": str(row[5]) if row[5] is not None else None,
        "version_evidence": str(row[6]) if row[6] is not None else None,
        "catalog_no": str(row[7]) if row[7] is not None else None,
        "barcode": str(row[8]) if row[8] is not None else None,
        "price": float(row[9]) if row[9] is not None else None,
        "currency": str(row[10]) if row[10] is not None else None,
        "source_url": str(row[11]) if row[11] is not None else None,
        "note": str(row[12]) if row[12] is not None else None,
    }


def _require_positive_int(value: object, field: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")


def _normalize_choice(value: object, allowed: frozenset[str], field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    normalized = value.strip().casefold()
    if normalized not in allowed:
        raise ValueError(f"unsupported {field}: {value}")
    return normalized


def _optional_observation_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text or None")
    normalized = value.strip()
    if len(normalized) > 2_000:
        raise ValueError(f"{field} exceeds the 2000-character limit")
    return normalized or None


def _normalize_optional_price(value: float | None) -> float | None:
    if value is None:
        return None
    if type(value) not in (float, int) or isinstance(value, bool):
        raise ValueError("price must be numeric or None")
    normalized = float(value)
    if not normalized >= 0.0 or normalized == float("inf"):
        raise ValueError("price must be a finite non-negative number")
    return normalized


def _normalize_optional_currency(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("currency must be text or None")
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", normalized):
        raise ValueError("currency must be a three-letter code")
    return normalized


def _normalize_observation_url(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("source_url must be text or None")
    raw = value.strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source_url must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source_url must not contain credentials")
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() in _SENSITIVE_URL_QUERY_KEYS:
            raise ValueError("source_url must not contain a credential query parameter")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _normalize_observed_at(value: datetime | str | None) -> str:
    if value is None:
        timestamp = datetime.now(UTC)
    elif isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("observed_at datetime must include a timezone")
        timestamp = value.astimezone(UTC)
    elif isinstance(value, str):
        raw = value.strip()
        try:
            timestamp = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
        if timestamp.tzinfo is None:
            raise ValueError("observed_at timestamp must include a timezone")
        timestamp = timestamp.astimezone(UTC)
    else:
        raise TypeError("observed_at must be datetime, ISO text, or None")
    return timestamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _find_tesseract() -> Path | None:
    discovered = shutil.which("tesseract")
    if discovered:
        return Path(discovered)
    return _TESSERACT_DEFAULT_PATH if _TESSERACT_DEFAULT_PATH.is_file() else None


def _available_tesseract_languages(
    executable: Path | None, *, tessdata_dir: Path | None = None
) -> tuple[str, ...]:
    if executable is None:
        return ()
    command = [str(executable)]
    if tessdata_dir is not None:
        command.extend(["--tessdata-dir", str(tessdata_dir)])
    command.append("--list-langs")
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return ()
    return _parse_tesseract_languages(result.stdout)


def _parse_tesseract_languages(stdout: str) -> tuple[str, ...]:
    languages = tuple(
        line.strip()
        for line in stdout.splitlines()
        if line.strip() and "list of" not in line.casefold()
    )
    return tuple(sorted(set(languages)))


def _decode_barcode(path: Path) -> tuple[str | None, str]:
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except ImportError:
        return None, "barcode:unavailable"
    try:
        with Image.open(path) as image:
            decoded = decode(image)
    except (OSError, RuntimeError, ValueError):
        # Local image failures remain auditable, not fatal.
        return None, "barcode:failed"
    barcodes = {
        value
        for item in decoded
        if str(getattr(item, "type", "")).upper().replace("-", "") in _BARCODE_TYPES
        if (value := _clean_barcode(getattr(item, "data", b""))) is not None
    }
    if not barcodes:
        return None, "barcode:none"
    return min(barcodes), "barcode:found"


def _extract_ocr_text(
    path: Path,
    *,
    executable: Path | None,
    languages: tuple[str, ...],
    tessdata_dir: Path | None = None,
) -> tuple[str, str]:
    selected_languages = tuple(
        language for language in ("eng", "jpn", "chi_sim") if language in languages
    )
    if executable is None or not selected_languages:
        return "", "ocr:unavailable"
    command = [str(executable)]
    if tessdata_dir is not None:
        command.extend(["--tessdata-dir", str(tessdata_dir)])
    command.extend(
        [str(path), "stdout", "--psm", "6", "-l", "+".join(selected_languages)]
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", "ocr:failed"
    if result.returncode != 0:
        return "", "ocr:failed"
    return (
        result.stdout.strip()[:_MAX_EXTRACTED_TEXT_CHARS],
        f"ocr:{'+'.join(selected_languages)}",
    )


def _clean_barcode(value: object) -> str | None:
    if isinstance(value, bytes):
        text = value.decode("ascii", errors="ignore")
    elif isinstance(value, str):
        text = value
    else:
        return None
    normalized = re.sub(r"\D", "", text)
    return normalized if _is_ean_jan(normalized) else None


def _is_ean_jan(value: str) -> bool:
    return value.isdecimal() and len(value) in {8, 12, 13}
