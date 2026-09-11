from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_reference_panel_has_latest_market_and_profile_contract() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "discovery-ui.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "styles" / "kuro.css").read_text(encoding="utf-8")

    assert 'id="referenceXianyuLatest"' in html
    assert 'id="referenceWameijiLatest"' in html
    assert 'id="referenceProfileList"' in html
    assert 'apiGet("/api/reference-memory/profiles?limit=3")' in script
    assert "latest_market_coverage" in script
    assert "missing_evidence" in script
    assert ".reference-profiles" in styles
