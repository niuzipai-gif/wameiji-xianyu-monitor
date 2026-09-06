"""P5.4+: live AI criteria generation smoke (manual run).

This test exercises the real Kuro AI plumbing (no monkey-patching) end-to-end:

  - reads prompts/macbook_criteria.txt as the few-shot anchor;
  - calls _call_once against the configured primary or fallback provider;
  - validates the returned criteria file body is non-empty and plausibly Chinese.

Run explicitly from PowerShell after configuring an AI provider::

    $env:CD_MONITOR_RUN_LIVE_TESTS = "1"
    python -m pytest -q tests/test_p54_task_generate_live.py -m live -s
    Remove-Item Env:CD_MONITOR_RUN_LIVE_TESTS

This test makes a real network call. It is marked ``live`` and skipped at
module import unless ``CD_MONITOR_RUN_LIVE_TESTS=1`` is explicitly set.
Selecting this filename or using ``-m live`` alone does not opt in.
"""
from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.live

if os.environ.get("CD_MONITOR_RUN_LIVE_TESTS") != "1":
    pytest.skip(
        "Set CD_MONITOR_RUN_LIVE_TESTS=1 to authorize real AI requests.",
        allow_module_level=True,
    )


def test_live_generate_criteria_against_configured_provider(monkeypatch) -> None:
    from cd_monitor.infrastructure.external.ai_client import (
        get_fallback_client, get_primary_client,
    )
    if not (get_primary_client() or get_fallback_client()):
        pytest.skip("no AI provider configured in this environment")

    from cd_monitor.services.scraper import _prompt_utils

    async def _collect_progress(steps):
        async def _cb(step_key, message):
            steps.append((step_key, message))
        return _cb

    import asyncio

    async def _run() -> str:
        steps = []
        cb = await _collect_progress(steps)
        body = await _prompt_utils.generate_criteria(
            user_description=(
                "寻找日系二手 CD（アイドルマスター シンデレラガールズ 新品未开封 "
                "初回限定盤、附赠写真集），只收个人卖家。"
            ),
            reference_file_path=os.path.join("prompts", "macbook_criteria.txt"),
            progress_callback=cb,
        )
        assert any(s[0] == "llm" for s in steps), steps
        return body

    body = asyncio.run(_run())
    assert body and body.strip(), "AI returned empty body"
    # The META_PROMPT asks the model to imitate the EagleEye style with
    # `[V6.x 核心升级]` markers — both Usagi's actual reference file and the
    # generated content tend to surface these markers.
    assert any(
        marker in body for marker in (
            "[V6.3", "[V6.4", "画像优先原则", "一票否决",
            "卖家信用", "危险信号",
        )
    ), f"unexpected AI body shape: head={body[:200]!r}"
    # Cross-check the meta-prompt content is hidden (model should NOT echo the
    # META_PROMPT_TEMPLATE — Kuro does pass it through, so this assertion is
    # lenient: just ensure no raw template marker leaks back at the head.
    assert "META_PROMPT_TEMPLATE" not in body
    # The few-shot anchor must remain a positive example, not a literal copy.
    assert "MacBook Air M1" not in body
