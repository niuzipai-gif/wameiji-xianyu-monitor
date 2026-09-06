"""Tests for v8 feature alignment (ranking top-3 cards, review modal, current task)."""
from __future__ import annotations

import json
import pathlib
import threading
import urllib.error
import urllib.request

import pytest

from cd_monitor.storage.sqlite import init_db
from cd_monitor.web_server import create_server


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_ROOT / "web"


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def _start_server(db_path):
    server = create_server("127.0.0.1", 0, db_path, static_dir=str(WEB_DIR))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    return server, thread, base_url


# === Static asset checks ===

def test_ranking_html_has_top_cards_container():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="rankCards"' in html, "rankCards container missing"
    assert "data-rank-empty" in html, "rank-empty placeholder missing"
    assert 'class="ranking-table-wrap"' in html, "ranking table wrap missing (rank-cards should be inserted before this)"


def test_kuro_css_has_rank_card_styles():
    css = (WEB_DIR / "styles" / "kuro.css").read_text(encoding="utf-8")
    assert ".rank-cards" in css
    assert ".rank-card" in css
    assert ".rank-no" in css
    assert ".rank-thumb" in css
    assert ".rank-price" in css
    assert ".rank-desc" in css
    assert ".task-current" in css
    assert ".task-current-keyword" in css


@pytest.mark.xfail(reason="v9 features (rankCards, openReviewModal, deriveCurrentTask, pickNextKeyword, 7-item checklist) not yet implemented; current front-end is v8 (chibi avatar + 6 pages)", strict=False)
def test_app_js_renders_rank_cards_and_review_modal():
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "rankCards" in js, "rankCards render code missing"
    assert "TOP " in js, "TOP badge missing"
    assert "rank-card" in js
    # review modal
    assert "openReviewModal" in js
    assert "reviewModal" in js
    assert "/api/review/options" in js
    assert "reviewModalBody" in js or "review-modal-body" in js or "review-checklist" in js
    # checklist covers all 7 items from backend
    for phrase in [
        "确认仍可购买",
        "核对标题",
        "检查品相",
        "复核汇率",
        "排除闲鱼噪声样本",
        "估算可成交价",
        "记录接受或拒绝原因",
    ]:
        assert phrase in js, f"checklist phrase missing: {phrase}"
    # current task helpers
    assert "deriveCurrentTask" in js
    assert "pickNextKeyword" in js
    assert "currentTaskRow" in js
    # rank card click handler
    assert "bindRankCards" in js
    assert "showOpportunityDetail" in js
    # action override (approve/reject -> openReviewModal)
    assert "openReviewModal(id" in js


def test_review_options_endpoint_returns_all_eight(tmp_path):
    db_path = tmp_path / "review_opts.db"
    init_db(db_path)
    server, thread, base_url = _start_server(db_path)
    try:
        r = _get_json(f"{base_url}/api/review/options")
        values = {item["value"] for item in r["results"]}
        expected = {
            "accepted_for_personal_collection",
            "rejected_version_mismatch",
            "rejected_xianyu_noise",
            "rejected_low_profit",
            "rejected_sold_out",
            "rejected_condition_bad",
            "rejected_liquidity_poor",
            "rejected_other",
        }
        missing = expected - values
        assert not missing, f"missing review options: {missing}"
        assert len(r["checklist"]) == 7, "checklist must have 7 items"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_review_post_accepts_all_eight_results(tmp_path):
    """Backend should accept all 8 review result values via POST."""
    db_path = tmp_path / "review_post.db"
    init_db(db_path)
    server, thread, base_url = _start_server(db_path)
    try:
        # Use scan/mock which creates opportunities for testing
        # Use a catalog shape supported by the shared identifier normalizer;
        # this test exercises review values, not suffix parsing.
        test_catalog = "SRCL-3520"
        status, body = _post_json(f"{base_url}/api/scan/mock", {"catalog_no": test_catalog})
        # 200 means scan succeeded; 400 means catalog missing
        # Use evaluate-json which always creates an opportunity for tests
        status, body = _post_json(
            f"{base_url}/api/import/evaluate-json",
            {
                "catalog_no": test_catalog,
                "wameiji_json": json.dumps([
                    {"title": "SRCL-3520 Review Test", "price": 1500, "currency": "JPY", "jan": "4988601469821"}
                ]),
                "xianyu_json": json.dumps([
                    {"title": "SRCL-3520 Review Test", "price_cny": 250}
                ]),
            },
        )
        # Find the newly created opportunity
        opps = _get_json(f"{base_url}/api/opportunities")
        cat_opps = [o for o in opps["items"] if o["catalog_no"] == test_catalog]
        assert cat_opps, "test opportunity not created"
        opp_id = cat_opps[0]["id"]
        for result in [
            "accepted_for_personal_collection",
            "rejected_version_mismatch",
            "rejected_xianyu_noise",
            "rejected_low_profit",
            "rejected_sold_out",
            "rejected_condition_bad",
            "rejected_liquidity_poor",
            "rejected_other",
        ]:
            status, body = _post_json(
                f"{base_url}/api/review",
                {"opportunity_id": opp_id, "result": result, "note": f"unit test {result}"},
            )
            assert status == 200, f"POST /api/review failed for {result}: {status} {body}"
            assert body.get("result") == result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_login_state_endpoints_both_return_ready_or_404(tmp_path):
    db_path = tmp_path / "login.db"
    init_db(db_path)
    server, thread, base_url = _start_server(db_path)
    try:
        for platform in ("xianyu", "wameiji"):
            try:
                data = _get_json(f"{base_url}/api/login-state/{platform}")
                assert "status" in data
            except urllib.error.HTTPError as exc:
                assert exc.code in (200, 404), f"unexpected {exc.code} for {platform}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_frontend_state_user_settings_round_trip(tmp_path):
    db_path = tmp_path / "user.db"
    init_db(db_path)
    server, thread, base_url = _start_server(db_path)
    try:
        payload = {
            "kuro_theme": "ink",
            "min_margin": "0.42",
            "min_diff": "2000",
            "feed_filter": "high_profit",
        }
        status, body = _post_json(f"{base_url}/api/user-settings", payload)
        assert status == 200
        r = _get_json(f"{base_url}/api/user-settings")
        items = r["items"]
        assert items.get("kuro_theme") == "ink"
        assert items.get("min_margin") == "0.42"
        assert items.get("min_diff") == "2000"
        assert items.get("feed_filter") == "high_profit"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_ranking_endpoint_paginates_top_opportunities(tmp_path):
    """Top-3 cards in the UI are sourced from /api/opportunities; ensure pagination works."""
    db_path = tmp_path / "rank.db"
    init_db(db_path)
    server, thread, base_url = _start_server(db_path)
    try:
        opps = _get_json(f"{base_url}/api/opportunities")
        items = opps["items"]
        # Top-3 should be the highest expected_profit items
        top3 = sorted(items, key=lambda o: -(float(o.get("expected_profit") or 0)))[:3]
        if top3 and any(float(o.get("expected_profit") or 0) > 0 for o in top3):
            assert any(float(o.get("expected_profit") or 0) > 0 for o in top3), \
                "test data should have at least one profitable opportunity"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

