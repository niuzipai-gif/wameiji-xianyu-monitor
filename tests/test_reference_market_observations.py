from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from cd_monitor.services.reference_memory import (
    ExtractedReferenceSample,
    build_reference_product_profile,
    import_reference_samples,
    list_reference_market_observations,
    record_reference_market_observation,
    reference_memory_status,
)


@dataclass
class _FixtureExtractor:
    def extract(self, _path: Path) -> ExtractedReferenceSample:
        return ExtractedReferenceSample(
            checksum_sha256="f" * 64,
            barcode="4547366558180",
            extracted_text="milet Walkin In My Lane",
            extraction_state="fixture",
        )


def _seed_reference_product(tmp_path: Path) -> tuple[Path, int]:
    db_path = tmp_path / "reference.db"
    folder = tmp_path / "samples"
    folder.mkdir()
    (folder / "milet.png").write_bytes(b"fixture")
    import_reference_samples(db_path, folder, extractor=_FixtureExtractor())
    with sqlite3.connect(db_path) as conn:
        product_id = int(conn.execute("SELECT id FROM reference_products").fetchone()[0])
    return db_path, product_id


def test_market_observation_is_timestamped_and_does_not_change_positive_identity(
    tmp_path: Path,
) -> None:
    db_path, product_id = _seed_reference_product(tmp_path)

    observation = record_reference_market_observation(
        db_path,
        product_id=product_id,
        market="wameiji",
        observation_state="price_unfavorable",
        observed_title="milet Walkin In My Lane 初回限定盤",
        version_evidence="初回限定盤",
        barcode="4547366558180",
        price=9_999.0,
        currency="JPY",
        source_url="https://meruki.example/item/milet",
        note="当前价格不适合采购；仅记录本次观察。",
    )

    assert observation["market"] == "wameiji"
    assert observation["observation_state"] == "price_unfavorable"
    assert observation["observed_at"].endswith("Z")
    assert observation["price"] == 9_999.0
    assert observation["currency"] == "JPY"
    assert observation["source_url"] == "https://meruki.example/item/milet"
    assert list_reference_market_observations(db_path, product_id=product_id) == [observation]

    with sqlite3.connect(db_path) as conn:
        product = conn.execute(
            "SELECT stable_key, barcode, sample_count FROM reference_products WHERE id = ?",
            (product_id,),
        ).fetchone()
    assert product == ("jan:4547366558180", "4547366558180", 1)

    status = reference_memory_status(db_path)
    assert status["product_count"] == 1
    assert status["market_observation_count"] == 1
    assert status["market_observation_states"] == {"price_unfavorable": 1}


def test_reference_product_profile_uses_latest_market_evidence_and_coverage(
    tmp_path: Path,
) -> None:
    db_path, product_id = _seed_reference_product(tmp_path)

    record_reference_market_observation(
        db_path,
        product_id=product_id,
        market="xianyu",
        observation_state="login_required",
        observed_at="2026-09-10T00:00:00+00:00",
    )
    current_found = record_reference_market_observation(
        db_path,
        product_id=product_id,
        market="xianyu",
        observation_state="found",
        catalog_no="SECL-9999",
        price=9_999,
        currency="CNY",
        source_url="https://goofish.example/item/milet",
        observed_at="2026-09-11T00:00:00+00:00",
    )

    profile = build_reference_product_profile(db_path, product_id=product_id)
    assert profile["catalog_numbers"] == ["SECL-9999"]
    assert profile["markets"] == {"wameiji": None, "xianyu": current_found}
    assert profile["missing_evidence"] == ["wameiji:market_observation"]

    status = reference_memory_status(db_path)
    assert status["market_observation_states"] == {"found": 1, "login_required": 1}
    assert status["latest_market_coverage"] == {
        "xianyu": {
            "covered_product_count": 1,
            "unobserved_product_count": 0,
            "states": {"found": 1},
            "latest_observed_at": "2026-09-11T00:00:00Z",
        },
        "wameiji": {
            "covered_product_count": 0,
            "unobserved_product_count": 1,
            "states": {},
            "latest_observed_at": None,
        },
    }


def test_reference_product_profile_breaks_same_timestamp_ties_by_latest_record(
    tmp_path: Path,
) -> None:
    db_path, product_id = _seed_reference_product(tmp_path)
    observed_at = "2026-09-11T00:00:00+00:00"

    record_reference_market_observation(
        db_path,
        product_id=product_id,
        market="wameiji",
        observation_state="found",
        observed_title="first listing",
        observed_at=observed_at,
    )
    second = record_reference_market_observation(
        db_path,
        product_id=product_id,
        market="wameiji",
        observation_state="not_currently_listed",
        observed_title="second listing",
        observed_at=observed_at,
    )

    assert build_reference_product_profile(db_path, product_id=product_id)["markets"]["wameiji"] == second


@pytest.mark.parametrize(
    ("market", "state"),
    [
        ("other", "found"),
        ("xianyu", "rejected"),
    ],
)
def test_market_observation_rejects_unknown_market_or_state(
    tmp_path: Path, market: str, state: str
) -> None:
    db_path, product_id = _seed_reference_product(tmp_path)

    with pytest.raises(ValueError):
        record_reference_market_observation(
            db_path,
            product_id=product_id,
            market=market,
            observation_state=state,
        )
