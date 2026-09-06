"""Tests for the `cd-monitor task-generate` CLI subcommand.

Mirrors the P5.4+ HTTP /api/watchlist/generate behavior so users can AI-generate
a task without running the web server. Two execution paths:

  - keyword mode:    synchronously creates a watch row, exits 0 with watch_id.
  - ai mode:         spawns the 6-step background job, returns 202-equivalent
                     snapshot via JSON. With --watch, polls until terminal.

These tests are deterministic — we monkey-patch the late-bound
`cd_monitor.services.scraper._prompt_utils.generate_criteria` so no real LLM
call is made.
"""
from __future__ import annotations

import argparse
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock


def _make_args(**overrides) -> argparse.Namespace:
    base = dict(
        task_name="端到端测试任务",
        keyword="end2end_test_keyword",
        description="寻找日系二手 CD 初回限定盤",
        decision_mode="ai",
        reference_file="prompts/macbook_criteria.txt",
        prompts_dir="prompts",
        watch=False,
        poll_interval=0.05,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


async def _fake_generate_criteria(user_description, reference_file_path,
                                   progress_callback=None, **_kw):
    """Async mock that mimics _prompt_utils.generate_criteria with progress callbacks."""
    if progress_callback:
        await progress_callback("reference", "正在读取参考文件。")
        await progress_callback("prompt", "正在构建发送给 AI 的指令。")
        await progress_callback("llm", "正在调用 AI 生成分析标准。")
    return (
        "## 任务目标\n识别高价值日系 CD 初回限定盤。\n\n"
        "## 关键筛选条件\n- 初回限定盤\n- 盘面无裂\n- 附写真集\n"
    )


def _sync_fake_generate_criteria(user_description, reference_file_path,
                                  progress_callback=None, **_kw):
    """Sync mock — tests that don't exercise the async AI path use this."""
    return "fake criteria text"


class CliTaskGenerateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="cli_task_gen_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.original_cwd = os.getcwd()
        # AI task generation intentionally requires a persisted account
        # snapshot. Keep this CLI test deterministic by providing the same
        # explicit root state used by the pre-flight contract tests, while
        # isolating generated prompts and DB files from the repository.
        (Path(self.tmpdir) / "data").mkdir()
        (Path(self.tmpdir) / "data" / "xianyu_state.json").write_text("{}", encoding="utf-8")
        (Path(self.tmpdir) / "prompts").mkdir()
        (Path(self.tmpdir) / "prompts" / "macbook_criteria.txt").write_text(
            "# deterministic test criteria\n", encoding="utf-8"
        )
        os.chdir(self.tmpdir)
        self.addCleanup(os.chdir, self.original_cwd)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.cli = importlib.import_module("cd_monitor.cli")

    def test_keyword_mode_creates_watch_synchronously(self) -> None:
        args = _make_args(decision_mode="keyword", description="")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.cli._run_task_generate(args, self.db_path)
        self.assertEqual(rc, 0, buf.getvalue())
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["mode"], "keyword")
        self.assertEqual(payload["task_name"], "端到端测试任务")
        self.assertEqual(payload["keyword"], "end2end_test_keyword")
        self.assertIsInstance(payload["watch_id"], int)

    def test_ai_mode_requires_description(self) -> None:
        args = _make_args(decision_mode="ai", description="")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.cli._run_task_generate(args, self.db_path)
        self.assertEqual(rc, 2)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["error"], "description_required")

    def test_ai_mode_requires_reference_file(self) -> None:
        args = _make_args(reference_file="prompts/_definitely_missing_xyz.txt")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.cli._run_task_generate(args, self.db_path)
        self.assertEqual(rc, 2)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["error"], "reference_not_found")

    def test_ai_mode_requires_task_name(self) -> None:
        args = _make_args(task_name="")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.cli._run_task_generate(args, self.db_path)
        self.assertEqual(rc, 2)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["error"], "task_name_required")

    def test_ai_mode_fire_and_forget_returns_job_snapshot(self) -> None:
        args = _make_args(watch=False)
        with mock.patch(
            "cd_monitor.services.scraper._prompt_utils.generate_criteria",
            _fake_generate_criteria,
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = self.cli._run_task_generate(args, self.db_path)
        self.assertEqual(rc, 0, buf.getvalue())
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["task_name"], "端到端测试任务")
        self.assertEqual(payload["decision_mode"], "ai")
        self.assertEqual(payload["status"], "running")
        self.assertEqual(len(payload["steps"]), 6)
        keys = [s["key"] for s in payload["steps"]]
        self.assertEqual(keys, ["prepare", "reference", "prompt", "llm", "persist", "task"])

    def test_ai_mode_with_watch_polls_until_completion(self) -> None:
        args = _make_args(watch=True, poll_interval=0.02)
        with mock.patch(
            "cd_monitor.services.scraper._prompt_utils.generate_criteria",
            _fake_generate_criteria,
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = self.cli._run_task_generate(args, self.db_path)
        self.assertEqual(rc, 0, buf.getvalue())
        text = buf.getvalue()
        # Walk backwards through stdout lines; the final JSON dump is the
        # last machine-readable summary (after any ::STEP:: progress lines
        # and the "任务生成中" header).
        final = None
        for line in text.splitlines()[::-1]:
            line = line.strip()
            if not line or line.startswith("::STEP::") or line.startswith("任务"):
                continue
            try:
                final = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        self.assertIsNotNone(final, "no JSON dump found in:\n" + text)
        self.assertEqual(final["status"], "completed")
        self.assertIsInstance(final["watch_id"], int)
        self.assertTrue(final["criteria_path"].endswith("_criteria.txt"))


if __name__ == "__main__":
    unittest.main()
