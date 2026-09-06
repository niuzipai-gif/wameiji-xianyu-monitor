from cd_monitor.core.models import WatchItem
from cd_monitor.sources.mock import MockWameijiAdapter, MockXianyuAdapter


def test_mock_wameiji_filters_by_catalog_no(tmp_path) -> None:
    path = tmp_path / "wameiji.json"
    path.write_text(
        """[
          {"source":"wameiji","title":"Artist SRCL-3520","price":1000},
          {"source":"wameiji","title":"Other ABCD-1234","price":1000}
        ]""",
        encoding="utf-8",
    )

    items = MockWameijiAdapter(path).search(WatchItem(catalog_no="SRCL-3520"))

    assert len(items) == 1
    assert items[0].title == "Artist SRCL-3520"


def test_mock_xianyu_filters_by_catalog_no(tmp_path) -> None:
    path = tmp_path / "xianyu.json"
    path.write_text(
        """[
          {"catalog_no":"SRCL-3520","title":"Artist SRCL-3520","price_cny":100},
          {"catalog_no":"ABCD-1234","title":"Other ABCD-1234","price_cny":100}
        ]""",
        encoding="utf-8",
    )

    samples = MockXianyuAdapter(path).search_samples(WatchItem(catalog_no="SRCL-3520"))

    assert len(samples) == 1
    assert samples[0].title == "Artist SRCL-3520"


def test_mock_wameiji_fills_missing_source_field(tmp_path) -> None:
    """Regression: when the raw JSON row lacks a `source` field, the adapter
    must still produce a valid MarketItem (default to ``"mock"``) rather than
    raising ``TypeError: missing required positional argument: 'source'``."""
    path = tmp_path / "wameiji_no_source.json"
    path.write_text(
        """[
          {"title":"Artist SRCL-3520","price":1000,"currency":"JPY"}
        ]""",
        encoding="utf-8",
    )

    items = MockWameijiAdapter(path).search(WatchItem(catalog_no="SRCL-3520"))

    assert len(items) == 1
    assert items[0].source == "mock"
    assert items[0].title == "Artist SRCL-3520"


def test_mock_wameiji_preserves_existing_source_field(tmp_path) -> None:
    """The adapter must keep the row's own ``source`` value when present
    (real fixtures tag rows with the originating platform name)."""
    path = tmp_path / "wameiji_with_source.json"
    path.write_text(
        """[
          {"source":"wameiji","title":"Artist SRCL-3520","price":1000}
        ]""",
        encoding="utf-8",
    )

    items = MockWameijiAdapter(path).search(WatchItem(catalog_no="SRCL-3520"))

    assert len(items) == 1
    assert items[0].source == "wameiji"
