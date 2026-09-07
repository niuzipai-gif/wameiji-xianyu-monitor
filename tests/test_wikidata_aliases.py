from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

from cd_monitor.sources import wikidata_aliases
from cd_monitor.sources.wikidata_aliases import WikidataTitleAliasResolver


def test_resolver_returns_public_labels_only_for_an_exact_title_entity() -> None:
    calls: list[dict[str, list[str]]] = []

    def request_json(url: str) -> dict:
        params = parse_qs(urlparse(url).query)
        calls.append(params)
        if params["action"] == ["wbsearchentities"]:
            assert params["search"] == ["あくありうむ"]
            return {
                "search": [
                    {
                        "id": "Q114964778",
                        "label": "Aquarium",
                        "match": {"language": "ja", "text": "あくありうむ。"},
                    }
                ]
            }
        assert params["action"] == ["wbgetentities"]
        return {
            "entities": {
                "Q114964778": {
                    "labels": {
                        "ja": {"value": "あくありうむ。"},
                        "en": {"value": "Aquarium"},
                        "zh-hans": {"value": "AQUARIUM。"},
                    }
                }
            }
        }

    resolver = WikidataTitleAliasResolver(request_json=request_json)
    aliases = asyncio.run(
        resolver.resolve("【一部未使用】あくありうむ。 完全生産限定版 Switch ソフト")
    )

    assert [alias.value for alias in aliases] == ["Aquarium"]
    assert aliases[0].source == "wikidata"
    assert aliases[0].source_url == "https://www.wikidata.org/wiki/Q114964778"
    # The cache prevents a second pair of public requests for the same source title.
    assert asyncio.run(resolver.resolve("あくありうむ。 Switch")) == aliases
    assert len(calls) == 2


def test_resolver_rejects_a_non_exact_search_result() -> None:
    def request_json(_url: str) -> dict:
        return {
            "search": [
                {
                    "id": "Q999",
                    "label": "A different game",
                    "match": {"language": "ja", "text": "別のあくありうむ"},
                }
            ]
        }

    resolver = WikidataTitleAliasResolver(request_json=request_json)

    assert asyncio.run(resolver.resolve("あくありうむ。 Switch")) == []


def test_resolver_includes_entity_verified_language_aliases() -> None:
    def request_json(url: str) -> dict:
        params = parse_qs(urlparse(url).query)
        if params["action"] == ["wbsearchentities"]:
            return {
                "search": [
                    {
                        "id": "Q115775247",
                        "label": "薄桜鬼",
                        "match": {"language": "ja", "text": "薄桜鬼"},
                    }
                ]
            }
        assert params["props"] == ["labels|aliases"]
        return {
            "entities": {
                "Q115775247": {
                    "labels": {"en": {"value": "Hakuōki"}},
                    "aliases": {
                        "en": [
                            {"value": "Hakuoki"},
                            {"value": "Hakuouki"},
                        ]
                    },
                }
            }
        }

    aliases = asyncio.run(
        WikidataTitleAliasResolver(request_json=request_json).resolve("薄桜鬼DS 限定版")
    )

    assert [alias.value for alias in aliases] == ["Hakuōki", "Hakuoki", "Hakuouki"]


def test_resolver_uses_its_configured_timeout_for_the_default_requester(monkeypatch) -> None:
    timeouts: list[int] = []

    def request_json(url: str, *, timeout_seconds: int) -> dict:
        timeouts.append(timeout_seconds)
        params = parse_qs(urlparse(url).query)
        if params["action"] == ["wbsearchentities"]:
            return {
                "search": [
                    {
                        "id": "Q1",
                        "label": "あくありうむ。",
                        "match": {"text": "あくありうむ。"},
                    }
                ]
            }
        return {"entities": {"Q1": {"labels": {"en": {"value": "Aquarium"}}}}}

    monkeypatch.setattr(wikidata_aliases, "_request_json", request_json)
    resolver = WikidataTitleAliasResolver(timeout_seconds=3)

    assert [alias.value for alias in asyncio.run(resolver.resolve("あくありうむ。"))] == [
        "Aquarium"
    ]
    assert timeouts == [3, 3]
