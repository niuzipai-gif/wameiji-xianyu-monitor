"""Tests for cd_monitor.services.prompt_composer."""
from __future__ import annotations

import json
from pathlib import Path

from cd_monitor.services.prompt_composer import (
    DEFAULT_BASE_PROMPT_PATH,
    PromptContext,
    compose_for_task,
    compose_prompt,
)


def test_prompt_context_to_dict_round_trip():
    ctx = PromptContext(
        catalog_no="SRCL-1234",
        platform="wameiji",
        wameiji_price_jpy=12000,
        xianyu_reference_price_cny=600,
        xianyu_sample_count=5,
        match_confidence=0.85,
        required_keywords=["\u521d\u56de", "\u5e2f\u4ed8\u304d"],
        excluded_keywords=["\u50b7"],
    )
    d = ctx.to_dict()
    assert d["catalog_no"] == "SRCL-1234"
    assert d["wameiji_price_jpy"] == 12000
    assert d["required_keywords"] == ["\u521d\u56de", "\u5e2f\u4ed8\u304d"]
    json.dumps(d, ensure_ascii=False)


def test_compose_prompt_with_explicit_base(tmp_path):
    base = tmp_path / "base.txt"
    base.write_text("BASE TEMPLATE\n", encoding="utf-8")
    ctx = PromptContext(catalog_no="CAT-A", platform="both")
    out = compose_prompt(ctx, base_path=str(base), project_root=str(tmp_path))
    assert out.text.startswith("BASE TEMPLATE")
    assert "Live task context" in out.text
    assert "CAT-A" in out.text
    assert out.warnings == []
    assert out.base_path is not None


def test_compose_prompt_missing_base_falls_back(tmp_path):
    ctx = PromptContext(catalog_no="CAT-B", platform="both")
    out = compose_prompt(
        ctx, base_path=str(tmp_path / "does-not-exist.txt"),
        project_root=str(tmp_path),
    )
    assert "Japanese second-hand market analyst" in out.text
    assert any("base prompt missing" in w for w in out.warnings)


def test_compose_prompt_with_criteria(tmp_path):
    base = tmp_path / "base.txt"
    base.write_text("BASE\n", encoding="utf-8")
    crit = tmp_path / "crit.txt"
    crit.write_text("Reject items without obi strip.", encoding="utf-8")
    ctx = PromptContext(catalog_no="CAT-C", platform="xianyu")
    out = compose_prompt(
        ctx,
        base_path=str(base),
        criteria_path=str(crit),
        project_root=str(tmp_path),
    )
    assert "Task-specific criteria" in out.text
    assert "Reject items without obi strip." in out.text
    assert out.warnings == []


def test_compose_prompt_missing_criteria_records_warning(tmp_path):
    base = tmp_path / "base.txt"
    base.write_text("BASE\n", encoding="utf-8")
    ctx = PromptContext(catalog_no="CAT-D", platform="both")
    out = compose_prompt(
        ctx,
        base_path=str(base),
        criteria_path=str(tmp_path / "missing.txt"),
        project_root=str(tmp_path),
    )
    assert "BASE" in out.text
    assert "Task-specific criteria" not in out.text
    assert any("criteria prompt missing" in w for w in out.warnings)


def test_compose_prompt_context_block_contains_prices(tmp_path):
    base = tmp_path / "base.txt"
    base.write_text("B\n", encoding="utf-8")
    ctx = PromptContext(
        catalog_no="CAT-E",
        platform="wameiji",
        wameiji_price_jpy=8500,
        xianyu_reference_price_cny=420,
    )
    out = compose_prompt(ctx, base_path=str(base), project_root=str(tmp_path))
    assert "8500" in out.text
    assert "420" in out.text


def test_compose_for_task_reads_task_dict(tmp_path):
    base = tmp_path / "base.txt"
    base.write_text("BASE-X\n", encoding="utf-8")
    crit = tmp_path / "crit.txt"
    crit.write_text("extra rules", encoding="utf-8")
    task = {
        "ai_prompt_base_file": "base.txt",
        "ai_prompt_criteria_file": "crit.txt",
    }
    ctx = PromptContext(catalog_no="CAT-F", platform="both")
    out = compose_for_task(task, ctx, project_root=str(tmp_path))
    assert "BASE-X" in out.text
    assert "extra rules" in out.text
    assert out.criteria_path is not None


def test_compose_for_task_missing_paths(tmp_path):
    task = {"ai_prompt_base_file": None, "ai_prompt_criteria_file": None}
    ctx = PromptContext(catalog_no="CAT-G", platform="both")
    out = compose_for_task(task, ctx, project_root=str(tmp_path))
    assert "Japanese second-hand market analyst" in out.text
    assert any("base prompt missing" in w for w in out.warnings)


def test_default_base_path_constant():
    assert DEFAULT_BASE_PROMPT_PATH.endswith(".txt")
