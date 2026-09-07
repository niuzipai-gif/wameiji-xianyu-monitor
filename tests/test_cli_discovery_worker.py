from __future__ import annotations

from pathlib import Path

from cd_monitor import cli
from cd_monitor.services.discovery_worker import WorkerRunResult


def test_discovery_worker_once_uses_local_browser_bindings(tmp_path: Path, monkeypatch, capsys) -> None:
    db_path = tmp_path / "selection.db"
    snapshot_root = tmp_path / "snapshots"
    calls: dict[str, object] = {}

    async def fetch_wameiji(_keyword: str):
        return []

    async def fetch_wameiji_detail(_item):
        return None

    async def fetch_xianyu(_query: str):
        return []

    def build_fetchers(_config, supplied_snapshot_root):
        calls["snapshot_root"] = Path(supplied_snapshot_root)
        return fetch_wameiji, fetch_wameiji_detail, fetch_xianyu

    class FakeWorker:
        def __init__(self, **kwargs) -> None:
            calls["worker_kwargs"] = kwargs

        async def run_once(self) -> WorkerRunResult:
            calls["run_once"] = int(calls.get("run_once", 0)) + 1
            return WorkerRunResult(scan_count=3, command_count=1)

    monkeypatch.setattr(cli, "build_browser_fetchers", build_fetchers, raising=False)
    monkeypatch.setattr(cli, "DiscoveryWorker", FakeWorker, raising=False)
    monkeypatch.setattr(cli, "command_client_from_environment", lambda: None, raising=False)

    exit_code = cli.main(
        [
            "--db",
            str(db_path),
            "discovery-worker",
            "--once",
            "--snapshot-dir",
            str(snapshot_root),
            "--poll-seconds",
            "17",
        ]
    )

    assert exit_code == 0
    assert calls["snapshot_root"] == snapshot_root
    assert calls["run_once"] == 1
    assert calls["worker_kwargs"]["db_path"] == str(db_path)
    assert '"scan_count": 3' in capsys.readouterr().out


def test_discovery_worker_once_reports_unexpected_worker_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    async def fetch_wameiji(_keyword: str):
        return []

    async def fetch_wameiji_detail(_item):
        return None

    async def fetch_xianyu(_query: str):
        return []

    class FailingWorker:
        def __init__(self, **_kwargs) -> None:
            pass

        async def run_once(self) -> WorkerRunResult:
            raise RuntimeError("temporary sqlite lock")

    monkeypatch.setattr(
        cli,
        "build_browser_fetchers",
        lambda _config, _root: (fetch_wameiji, fetch_wameiji_detail, fetch_xianyu),
        raising=False,
    )
    monkeypatch.setattr(cli, "DiscoveryWorker", FailingWorker, raising=False)
    monkeypatch.setattr(cli, "command_client_from_environment", lambda: None, raising=False)

    exit_code = cli.main(["--db", str(tmp_path / "selection.db"), "discovery-worker", "--once"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"status": "worker_error"' in output
    assert '"error_type": "RuntimeError"' in output
