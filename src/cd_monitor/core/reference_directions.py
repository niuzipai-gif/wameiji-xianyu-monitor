"""Pure, positive-only product-direction evidence for approved references.

The taxonomy indicates only that a product direction appears in a user's
approved reference samples.  It has no decision, market, price, or browser
dependencies and cannot turn absence of a direction into a negative label.
"""

from __future__ import annotations

import re
from typing import Final


REFERENCE_DIRECTION_LABELS: Final = {
    "physical_music": "实体音乐",
    "console_game": "游戏/视觉小说",
    "art_or_book": "画集/设定集/书籍",
    "doujin_or_anime": "同人/动画作品",
    "limited_or_first_edition": "初回/限定/特典",
}

_DIRECTION_PATTERNS: Final = (
    (
        "physical_music",
        re.compile(r"(?i)(?:\bcd\b|专辑|音楽|音樂|音乐|\balbum\b)"),
    ),
    (
        "console_game",
        re.compile(
            r"(?i)(?:\bswitch\b|ps[ -]?vita|playstation|ゲーム|\bgame\b|galgame|视觉小说|游戏|任天堂|nintendo)"
        ),
    ),
    (
        "art_or_book",
        re.compile(r"(?i)(?:画集|設定集|设定集|攻略本|\bbook\b|小説|(?<!视觉)小说)"),
    ),
    ("doujin_or_anime", re.compile(r"(?i)(?:同人|アニメ)")),
    (
        "limited_or_first_edition",
        re.compile(r"(?i)(?:初回|限定版|限定|特典|\blimited\b)"),
    ),
)


def extract_reference_directions(text: str | None) -> tuple[str, ...]:
    """Return direction keys found in one piece of product identity text."""

    normalized = _optional_text(text, field="text")
    return tuple(key for key, pattern in _DIRECTION_PATTERNS if pattern.search(normalized))


def build_candidate_direction_text(
    *,
    title: str | None = None,
    artist: str | None = None,
    edition: str | None = None,
    catalog_no: str | None = None,
    jan: str | None = None,
    raw_text: str | None = None,
) -> str:
    """Build candidate direction input without mixing a real title with page shell text."""

    identity_fields = (title, artist, edition, catalog_no, jan)
    structured = tuple(_optional_text(value, field="candidate field") for value in identity_fields)
    if any(value.strip() for value in structured):
        return " ".join(value.strip() for value in structured if value.strip())
    return _optional_text(raw_text, field="candidate field")


def _optional_text(value: str | None, *, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text or None")
    return value


__all__ = [
    "REFERENCE_DIRECTION_LABELS",
    "build_candidate_direction_text",
    "extract_reference_directions",
]
