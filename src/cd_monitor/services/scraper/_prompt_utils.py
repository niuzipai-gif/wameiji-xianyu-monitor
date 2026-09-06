"""Prompt utilities for the Kuro task-generation pipeline.

This module ports Usagi's `src/prompt_utils.py` (task-generation only — not the
scraping AI flow) onto Kuro's infrastructure. Specifically:

- Usagi's reference AI client is an `AIClient` class with
  `is_available()` / `refresh()` / `_call_ai(...)` / `close()` semantics.
- Kuro's `cd_monitor.infrastructure.external.ai_client` exposes
  *module-level* functions (`get_primary_client`, `get_fallback_client`,
  `_call_once`, ...) instead. The prior port at
  `services/scraper/_prompt_utils.py` was a copy-paste of the Usagi shape
  and failed at `from ... import AIClient`.

This rewrite adapts the `generate_criteria` callable to Kuro's module
shape so that the rest of the task-generation stack
(`_task_generation_service.py`, `_task_generation_runner.py`,
`_task_models.py`) can stay byte-identical to the Usagi ports.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Awaitable, Callable, Optional

import aiofiles

from cd_monitor.infrastructure.config.settings import (
    ai_settings,
    fallback_ai_settings,
)
from cd_monitor.infrastructure.external.ai_client import (
    _call_once,
    get_fallback_client,
    get_primary_client,
)


META_PROMPT_TEMPLATE = """
你是一位世界级的AI提示词工程大师。你的任务是根据用户提供的【购买需求】，模仿一个【参考范例】，为闲鱼监控机器人的AI分析模块（代号 EagleEye）生成一份全新的【分析标准】文本。

你的输出必须严格遵循【参考范例】的结构、语气和核心原则，但内容要完全针对用户的【购买需求】进行定制。最终生成的文本将作为AI分析模块的思考指南。

---
这是【参考范例】（`macbook_criteria.txt`）：
```text
{reference_text}
```
---

这是用户的【购买需求】：
```text
{user_description}
```
---

请现在开始生成全新的【分析标准】文本。请注意：
1.  **只输出新生成的文本内容**，不要包含任何额外的解释、标题或代码块标记。
2.  保留范例中的 `[V6.3 核心升级]`、`[V6.4 逻辑修正]` 等版本标记，这有助于保持格式一致性。
3.  将范例中所有与 "MacBook" 相关的内容，替换为与用户需求商品相关的内容。
4.  思考并生成针对新商品类型的“一票否决硬性原则”和“危险信号清单”。
"""


ProgressCallback = Callable[[str, str], Awaitable[None]]


async def _report_progress(
    progress_callback: Optional[ProgressCallback],
    step_key: str,
    message: str,
) -> None:
    if progress_callback:
        await progress_callback(step_key, message)


def is_ai_available() -> bool:
    """Replaces Usagi's `ai_client.is_available()` against Kuro's module shape."""
    return bool(get_primary_client() or get_fallback_client())


def refresh_ai_client() -> bool:
    """Replaces Usagi's `ai_client.refresh()` — Kuro reads env on every call."""
    return is_ai_available()


def _resolve_model_name() -> str:
    if ai_settings.is_configured():
        return ai_settings.model_name
    return fallback_ai_settings.model_name


def _read_reference_text(reference_file_path: str) -> str:
    try:
        with open(reference_file_path, "r", encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"参考文件未找到: {reference_file_path}")
    except IOError as exc:
        raise IOError(f"读取参考文件失败: {exc}")


async def _request_generated_text(prompt: str, *, model: str) -> str:
    """Call Kuro's `_call_once` against primary client (or fallback)."""
    print("正在调用AI生成新的分析标准，请稍候...")
    client = get_primary_client() or get_fallback_client()
    if client is None:
        raise RuntimeError("AI 客户端未初始化，无法生成分析标准。请检查 .env 配置。")
    messages = [{"role": "user", "content": prompt}]
    text, err = await _call_once(
        client,
        model=model,
        messages=messages,
        enable_json=False,
        temperature=0.5,
        timeout=120.0,
    )
    if err:
        raise RuntimeError(f"调用 AI 出错: {err}")
    if not text:
        raise RuntimeError("AI 返回为空。")
    print("AI 已成功生成内容。")
    return text.strip()


async def generate_criteria(
    user_description: str,
    reference_file_path: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> str:
    """Generate a new criteria-file body for the user's task description.

    Mirrors Usagi `src/prompt_utils.py::generate_criteria` with Kuro
    module-level AI plumbing. Returns the criteria body (not the file).
    """
    try:
        if not is_ai_available():
            refresh_ai_client()
        if not is_ai_available():
            raise RuntimeError("AI客户端未初始化，无法生成分析标准。请检查 .env 配置。")

        await _report_progress(progress_callback, "reference", "正在读取参考文件。")
        print(f"正在读取参考文件: {reference_file_path}")
        reference_text = _read_reference_text(reference_file_path)

        await _report_progress(progress_callback, "prompt", "正在构建发送给 AI 的指令。")
        print("正在构建发送给AI的指令...")
        prompt = META_PROMPT_TEMPLATE.format(
            reference_text=reference_text,
            user_description=user_description,
        )

        await _report_progress(progress_callback, "llm", "正在调用 AI 生成分析标准。")
        return await _request_generated_text(prompt, model=_resolve_model_name())
    except Exception as exc:
        print(f"AI 任务生成失败: {exc}")
        raise


__all__ = [
    "ProgressCallback",
    "generate_criteria",
    "is_ai_available",
    "refresh_ai_client",
]
