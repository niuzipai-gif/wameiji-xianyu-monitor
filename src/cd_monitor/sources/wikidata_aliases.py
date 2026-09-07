"""Free, exact-title aliases from Wikidata for cross-language marketplace search."""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cd_monitor.core.title_query import title_alias_lookup_query

_API_URL = "https://www.wikidata.org/w/api.php"
_ALIAS_LANGUAGES = ("en", "zh-hans", "zh")
_IDENTITY_RE = re.compile(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]+")

JsonRequester = Callable[[str], dict]


@dataclass(frozen=True, slots=True)
class ResolvedTitleAlias:
    """A title label whose source entity exactly matched the Japanese title."""

    value: str
    source: str
    source_url: str
    entity_id: str


class WikidataTitleAliasResolver:
    """Resolve a title through Wikidata without making a paid API call.

    A result is accepted only when Wikidata's returned matched text normalizes
    exactly to the product-only source title. Network failures and ambiguous
    hits deliberately return no aliases, so normal collection keeps working
    without turning a fuzzy metadata search into price evidence.
    """

    def __init__(
        self,
        *,
        request_json: JsonRequester | None = None,
        timeout_seconds: int = 8,
    ) -> None:
        self._request_json = request_json or (
            lambda url: _request_json(url, timeout_seconds=timeout_seconds)
        )
        self._cache: dict[str, list[ResolvedTitleAlias]] = {}

    async def resolve(self, source_title: str | None) -> list[ResolvedTitleAlias]:
        lookup_title = title_alias_lookup_query(source_title)
        lookup_key = _identity_key(lookup_title)
        if len(lookup_key) < 3:
            return []
        if lookup_key in self._cache:
            return list(self._cache[lookup_key])
        try:
            aliases = await asyncio.to_thread(self._resolve_sync, lookup_title, lookup_key)
        # This free, non-authoritative enrichment must never interrupt the
        # collector, including on malformed upstream response shapes.
        except Exception:  # noqa: BLE001
            aliases = []
        self._cache[lookup_key] = aliases
        return list(aliases)

    def _resolve_sync(
        self, lookup_title: str, lookup_key: str
    ) -> list[ResolvedTitleAlias]:
        search_payload = self._request_json(
            _build_url(
                {
                    "action": "wbsearchentities",
                    "search": lookup_title,
                    "language": "ja",
                    "limit": "5",
                    "format": "json",
                    "origin": "*",
                }
            )
        )
        entity_id = _find_exact_entity_id(search_payload, lookup_key)
        if entity_id is None:
            return []
        entity_payload = self._request_json(
            _build_url(
                {
                    "action": "wbgetentities",
                    "ids": entity_id,
                    "props": "labels|aliases",
                    "languages": "|".join(_ALIAS_LANGUAGES),
                    "format": "json",
                    "origin": "*",
                }
            )
        )
        return _aliases_from_entity(entity_payload, entity_id, lookup_key)


def _build_url(params: dict[str, str]) -> str:
    return f"{_API_URL}?{urlencode(params)}"


def _request_json(url: str, *, timeout_seconds: int = 8) -> dict:
    request = Request(
        url,
        headers={"User-Agent": "WameijiXianyuMonitor/1.0 (free-title-alias)"},
        method="GET",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _find_exact_entity_id(payload: dict, lookup_key: str) -> str | None:
    raw_results = payload.get("search", []) if isinstance(payload, dict) else []
    if not isinstance(raw_results, list):
        return None
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        match = result.get("match")
        matched_text = match.get("text") if isinstance(match, dict) else None
        label = result.get("label")
        if not any(
            _identity_key(value) == lookup_key
            for value in (matched_text, label)
            if isinstance(value, str)
        ):
            continue
        entity_id = result.get("id")
        if isinstance(entity_id, str) and entity_id:
            return entity_id
    return None


def _aliases_from_entity(
    payload: dict, entity_id: str, lookup_key: str
) -> list[ResolvedTitleAlias]:
    entities = payload.get("entities") if isinstance(payload, dict) else None
    entity = entities.get(entity_id) if isinstance(entities, dict) else None
    labels = entity.get("labels") if isinstance(entity, dict) else None
    if not isinstance(labels, dict):
        return []
    aliases_by_language = entity.get("aliases") if isinstance(entity, dict) else None

    aliases: list[ResolvedTitleAlias] = []
    seen: set[str] = set()
    for language in _ALIAS_LANGUAGES:
        values: list[str] = []
        label = labels.get(language)
        if isinstance(label, dict) and isinstance(label.get("value"), str):
            values.append(label["value"])
        language_aliases = (
            aliases_by_language.get(language)
            if isinstance(aliases_by_language, dict)
            else None
        )
        if isinstance(language_aliases, list):
            values.extend(
                value["value"]
                for value in language_aliases
                if isinstance(value, dict) and isinstance(value.get("value"), str)
            )
        for value in values:
            key = _identity_key(value)
            if not key or key == lookup_key or key in seen:
                continue
            seen.add(key)
            aliases.append(
                ResolvedTitleAlias(
                    value=value.strip(),
                    source="wikidata",
                    source_url=f"https://www.wikidata.org/wiki/{entity_id}",
                    entity_id=entity_id,
                )
            )
    return aliases


def _identity_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _IDENTITY_RE.sub("", normalized).casefold()
