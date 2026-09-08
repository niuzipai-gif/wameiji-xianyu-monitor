"""Source-neutral facts and eligibility rules for the dual-market board.

An observation is one rendered listing seen at one time.  It deliberately does
not contain a matched price from the other marketplace: comparisons are built
later from two independently eligible observations.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

MarketSource = Literal["wameiji", "xianyu"]
EvidenceLevel = Literal["search_card", "detail_verified"]


@dataclass(frozen=True, slots=True)
class ListingObservation:
    """Immutable evidence collected from one visible marketplace listing."""

    source: MarketSource
    source_listing_id: str
    canonical_product_key: str | None
    title: str
    price: float
    currency: str
    url: str
    image_url: str
    availability: str
    condition_group: str
    completeness: str
    evidence_level: EvidenceLevel
    captured_at: str
    raw_snapshot_path: str | None = None
    screenshot_path: str | None = None
    source_detail_fee: float | None = None


def is_eligible(observation: ListingObservation, *, source: MarketSource) -> bool:
    """Return whether an observation may participate in that side's lowest price.

    Wameiji's search cards are discovery hints only.  Xianyu search cards are
    valid asking-price evidence but remain labelled as such in later views.
    """

    if observation.source != source:
        return False
    if observation.availability != "available":
        return False
    if observation.completeness != "complete":
        return False
    if observation.price <= 0:
        return False
    if not all((observation.title.strip(), observation.url.strip(), observation.image_url.strip())):
        return False
    return source != "wameiji" or observation.evidence_level == "detail_verified"


def select_lowest_eligible(
    observations: Iterable[ListingObservation], *, source: MarketSource
) -> ListingObservation | None:
    """Choose the lowest valid current listing for one marketplace only."""

    candidates = [item for item in observations if is_eligible(item, source=source)]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item.price, item.source_listing_id))
