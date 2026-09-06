"""Per-task AI prompt composer.

Composes a single prompt string for the AI analysis stage by combining:
  1. A base prompt template (file path on disk, or default fallback)
  2. A task-specific criteria file (file path on disk, optional)
  3. A live context block with the current task inputs (catalog, prices,
     platform, etc.) so the AI has the data it needs to score.

The base + criteria files are user-editable, version-controllable text
artifacts. The composer does *not* touch the watchlist's required/excluded
keywords — those are enforced upstream as filter rules before the AI call.

If a referenced file does not exist, the composer falls back gracefully and
records the issue in the returned diagnostics so the caller can warn the
operator. We never raise here: a broken prompt path should not crash a
production scan.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Union

DEFAULT_BASE_PROMPT_PATH = "prompts/base_prompt.txt"


@dataclass(slots=True)
class PromptContext:
    """Live values the AI needs to evaluate this run."""

    catalog_no: str
    platform: str
    wameiji_price_jpy: Optional[float] = None
    xianyu_reference_price_cny: Optional[float] = None
    xianyu_sample_count: int = 0
    match_confidence: float = 0.0
    required_keywords: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "catalog_no": self.catalog_no,
            "platform": self.platform,
            "wameiji_price_jpy": self.wameiji_price_jpy,
            "xianyu_reference_price_cny": self.xianyu_reference_price_cny,
            "xianyu_sample_count": self.xianyu_sample_count,
            "match_confidence": self.match_confidence,
            "required_keywords": list(self.required_keywords),
            "excluded_keywords": list(self.excluded_keywords),
            **({"extra": dict(self.extra)} if self.extra else {}),
        }


@dataclass(slots=True)
class ComposedPrompt:
    text: str
    base_path: Optional[str]
    criteria_path: Optional[str]
    warnings: list[str] = field(default_factory=list)
    context: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "base_path": self.base_path,
            "criteria_path": self.criteria_path,
            "warnings": list(self.warnings),
            "context": self.context,
        }


def _read_text(path: Union[str, Path], *, must_exist: bool = True) -> tuple[Optional[str], Optional[str]]:
    """Return (text_or_none, error_or_none)."""
    p = Path(path)
    if not p.exists():
        if must_exist:
            return None, "file not found: " + str(p)
        return None, None
    try:
        return p.read_text(encoding="utf-8"), None
    except Exception as exc:
        return None, "read error: " + repr(exc)


def compose_prompt(
    context: PromptContext,
    *,
    base_path: Optional[Union[str, Path]] = None,
    criteria_path: Optional[Union[str, Path]] = None,
    project_root: Union[str, Path] = ".",
) -> ComposedPrompt:
    warnings: list[str] = []
    root = Path(project_root)

    resolved_base = base_path or DEFAULT_BASE_PROMPT_PATH
    base_abs = (root / resolved_base).resolve() if not Path(resolved_base).is_absolute() else Path(resolved_base)
    base_text, base_err = _read_text(base_abs)
    if base_text is None:
        warnings.append("base prompt missing: " + (base_err or ""))
        base_text = (
            "You are a Japanese second-hand market analyst. Given a catalog "
            "and prices, recommend whether to buy. Output JSON with fields: "
            "recommended, confidence, expected_sale_price_cny, rationale_cn, risk_labels."
        )

    criteria_text = ""
    if criteria_path:
        crit_abs = (root / criteria_path).resolve() if not Path(criteria_path).is_absolute() else Path(criteria_path)
        ctext, cerr = _read_text(crit_abs)
        if ctext is None:
            warnings.append("criteria prompt missing: " + (cerr or ""))
        else:
            criteria_text = "\n\n[Task-specific criteria]\n" + ctext.strip() + "\n"

    context_json = json.dumps(context.to_dict(), ensure_ascii=False, indent=2)
    context_block = (
        "\n\n[Live task context]\n" + context_json + "\n"
    )

    text = base_text.rstrip() + criteria_text + context_block
    return ComposedPrompt(
        text=text,
        base_path=str(base_abs),
        criteria_path=str(crit_abs) if criteria_path else None,
        warnings=warnings,
        context=context.to_dict(),
    )


def compose_for_task(
    task: dict,
    context: PromptContext,
    *,
    project_root: Union[str, Path] = ".",
) -> ComposedPrompt:
    """Convenience: pull base/criteria paths from a task dict."""
    return compose_prompt(
        context,
        base_path=task.get("ai_prompt_base_file"),
        criteria_path=task.get("ai_prompt_criteria_file"),
        project_root=project_root,
    )
