import asyncio

from cd_monitor.core.models import CostConfig, EvaluationConfig
from cd_monitor.services.live_capture_scan import capture_and_evaluate_live_html
from cd_monitor.storage.sqlite import init_db


def test_capture_and_evaluate_live_html_combines_captured_wameiji_and_xianyu(tmp_path) -> None:
    db_path = tmp_path / "live-html.db"
    snapshot_dir = tmp_path / "snapshots"
    init_db(db_path)
    calls = []

    async def fake_capture(source, _catalog_no, output_path, **kwargs):
        calls.append((source, kwargs))
        html = _wameiji_html() if source == "wameiji" else _xianyu_html()
        output_path.write_text(html, encoding="utf-8")
        return {
            "source": source,
            "status": "ok",
            "item_count": 1,
            "snapshot_path": str(output_path),
            "screenshot_path": str(kwargs.get("screenshot_path")),
            "network_log_path": str(kwargs.get("network_log_path")),
        }

    result = asyncio.run(
        capture_and_evaluate_live_html(
            "SRCL-3520",
            db_path,
            snapshot_dir,
            CostConfig(),
            EvaluationConfig(),
            state_file="data/xianyu_state.json",
            profile_dir=tmp_path / "profile",
            xianyu_profile_dir=tmp_path / "goofish-profile",
            capture_func=fake_capture,
        )
    )

    assert result["status"] == "ok"
    assert result["opportunity_count"] >= 1
    assert result["wameiji_capture"]["status"] == "ok"
    assert result["xianyu_capture"]["status"] == "ok"
    assert result["snapshot_path"]
    assert calls[0][0] == "wameiji"
    assert calls[0][1]["screenshot_path"] == snapshot_dir / "live_screenshots" / "SRCL-3520_wameiji.png"
    assert calls[0][1]["network_log_path"] == snapshot_dir / "live_network" / "SRCL-3520_wameiji.network.json"
    # Wameiji and Xianyu captures both receive profile_dir (WameijiRunner now consumes it).
    assert calls[0][1]["profile_dir"] == tmp_path / "profile"
    assert calls[1][0] == "xianyu"
    assert calls[1][1]["screenshot_path"] == snapshot_dir / "live_screenshots" / "SRCL-3520_xianyu.png"
    assert calls[1][1]["network_log_path"] == snapshot_dir / "live_network" / "SRCL-3520_xianyu.network.json"
    assert calls[1][1]["profile_dir"] == tmp_path / "profile"
    assert calls[1][1]["xianyu_profile_dir"] == tmp_path / "goofish-profile"


def test_capture_and_evaluate_live_html_stops_when_capture_needs_human(tmp_path) -> None:
    async def fake_capture(source, _catalog_no, output_path, **_kwargs):
        output_path.write_text("<html>请完成 CAPTCHA 安全验证后继续</html>", encoding="utf-8")
        return {
            "source": source,
            "status": "human_required",
            "error_type": "security_check",
            "item_count": 0,
            "snapshot_path": str(output_path),
        }

    result = asyncio.run(
        capture_and_evaluate_live_html(
            "SRCL-3520",
            tmp_path / "blocked.db",
            tmp_path / "snapshots",
            CostConfig(),
            EvaluationConfig(),
            capture_func=fake_capture,
        )
    )

    assert result["status"] == "human_required"
    assert result["opportunity_count"] == 0
    assert result["wameiji_capture"]["error_type"] == "security_check"


def _wameiji_html() -> str:
    return """
    <section>
      <article>
        <a href="https://example.invalid/item/1">Artist SRCL-3520 初回限定</a>
        <span>価格 1,200円</span>
        <span>販売中</span>
      </article>
    </section>
    """


def _xianyu_html() -> str:
    return """
    <main>
      <article>
        <a href="https://www.goofish.com/item?id=1">Artist SRCL-3520 初回限定</a>
        <span>到手价 260元</span>
      </article>
      <article>
        <a href="https://www.goofish.com/item?id=2">Artist SRCL-3520 初回限定 带特典</a>
        <span>到手价 280元</span>
      </article>
      <article>
        <a href="https://www.goofish.com/item?id=3">Artist SRCL-3520 初回限定 未拆</a>
        <span>到手价 300元</span>
      </article>
    </main>
    """
