from __future__ import annotations

import re


_CATALOG_RE = re.compile(r"\b([A-Za-z]{2,6})[-\s]?(\d{2,6})\b")
_JAN_RE = re.compile(r"(?<!\d)(\d{8}|\d{12}|\d{13})(?!\d)")


def normalize_catalog_no(value: str | None) -> str:
    if not value:
        return ""
    match = _CATALOG_RE.search(value.strip())
    if not match:
        return value.strip().upper().replace(" ", "-")
    return f"{match.group(1).upper()}-{match.group(2)}"


def normalize_catalog_no_compact(value: str | None) -> str:
    return normalize_catalog_no(value).replace("-", "")


def normalize_jan(value: str | None) -> str | None:
    if not value:
        return None
    matches = _JAN_RE.findall(re.sub(r"[^\d]", " ", value))
    return matches[0] if matches else None


def extract_catalog_candidates(text: str | None) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    values: list[str] = []
    for match in _CATALOG_RE.finditer(text):
        normalized = normalize_catalog_no(match.group(0))
        if normalized and normalized not in seen:
            seen.add(normalized)
            values.append(normalized)
    return values


def extract_jan_candidates(text: str | None) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    values: list[str] = []
    for match in _JAN_RE.finditer(text):
        jan = match.group(1)
        if jan not in seen:
            seen.add(jan)
            values.append(jan)
    return values
