"""
双路 AI 客户端门面。

主路：MiniMax M3（多模态，支持图片）。
备路：DeepSeek（纯文本，多模态失败/限流时降级）。

设计原则：
- 不修改参考项目的 ai_handler.py / ai_response_parser.py，而是新建一个轻量门面。
- 复用参考项目的 ai_request_compat.py：它能在 /responses 报 404 时自动回退
  到 /chat/completions，省去我们手写错误重试。
- 主备切换逻辑：调用主路失败 -> 备路仅喂文本 Prompt -> 再失败 -> 返回降级结构。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from cd_monitor.infrastructure.config.settings import (
    ai_settings, fallback_ai_settings,
)
from cd_monitor.infrastructure.external.ai_request_compat import (
    CHAT_COMPLETIONS_API_MODE,
    RESPONSES_API_MODE,
    build_ai_request_params,
    create_ai_response_async,
    is_chat_completions_api_unsupported_error,
    is_responses_api_unsupported_error,
    is_temperature_unsupported_error,
)

log = logging.getLogger("cd_monitor.ai")


# ------------------------------------------------------------------
# 客户端构建
# ------------------------------------------------------------------
def _build_client(api_key: Optional[str], base_url: str, proxy_url: Optional[str]) -> AsyncOpenAI:
    """构造一个 OpenAI 兼容 AsyncClient。

    httpx 会从环境变量读代理；这里显式设置一次确保生效。
    """
    if proxy_url:
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def get_primary_client() -> Optional[AsyncOpenAI]:
    if not ai_settings.is_configured():
        return None
    return _build_client(
        ai_settings.api_key, ai_settings.base_url, ai_settings.proxy_url,
    )


def get_fallback_client() -> Optional[AsyncOpenAI]:
    if not fallback_ai_settings.is_configured():
        return None
    return _build_client(
        fallback_ai_settings.api_key,
        fallback_ai_settings.base_url,
        fallback_ai_settings.proxy_url,
    )


# ------------------------------------------------------------------
# 图片编码辅助
# ------------------------------------------------------------------
def encode_image_as_data_url(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ------------------------------------------------------------------
# 内部：单次 AI 调用
# ------------------------------------------------------------------
EMPTY_MAX_RETRIES = 4  # P0 #4: retry budget for empty AI responses

async def _call_once(
    client: AsyncOpenAI,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    enable_json: bool = True,
    temperature: Optional[float] = 0.2,
    timeout: float = 60.0,
    max_empty_retries: int = EMPTY_MAX_RETRIES,
) -> Tuple[Optional[str], Optional[str]]:
    """请求 /responses（必要时回退 /chat/completions）；空响应会重试到上限。

    返回 (text_content, error_message)。成功时 error_message=None。
    当上游返回内容为空时（部分模型偶发返回空白响应），最多重试 4 次，再
    不行才放弃。这一行为是 P0 #4 与 Usagi ai-goofish-monitor 看齐。
    """
    api_mode = RESPONSES_API_MODE
    request_params: Dict[str, Any] = {}
    empty_attempts = 0

    while True:
        try:
            request_params = build_ai_request_params(
                api_mode,
                model=model,
                messages=messages,
                temperature=temperature,
                enable_json_output=enable_json,
            )
            response = await asyncio.wait_for(
                create_ai_response_async(client, api_mode, request_params),
                timeout=timeout,
            )
            # 提取文本
            text = _extract_text(response)
            if text is None:
                empty_attempts += 1
                if empty_attempts > max_empty_retries:
                    return None, "ai_returned_empty"
                # 重试同一组参数
                log.warning(
                    "AI 返回空响应（%s/%s），准备重试",
                    empty_attempts,
                    max_empty_retries,
                )
                continue
            return text, None
        except asyncio.TimeoutError:
            return None, "ai_timeout"
        except Exception as e:
            # 1) /responses 报 404 -> 切到 /chat/completions
            if api_mode == RESPONSES_API_MODE and is_responses_api_unsupported_error(e):
                api_mode = CHAT_COMPLETIONS_API_MODE
                continue
            # 2) /chat/completions 报 404 -> 已经没路可走
            if api_mode == CHAT_COMPLETIONS_API_MODE and is_chat_completions_api_unsupported_error(e):
                return None, f"api_not_supported: {e}"
            # 3) temperature 不支持 -> 移除后重试
            if temperature is not None and is_temperature_unsupported_error(e):
                temperature = None
                # 重新构参
                continue
            return None, f"ai_error: {e}"


def _extract_text(response: Any) -> Optional[str]:
    """兼容 Responses / ChatCompletions 两种 response 形状。"""
    # ChatCompletions 风格
    try:
        choices = getattr(response, "choices", None)
        if choices:
            msg = choices[0].message
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                return content
            # 部分网关把内容塞在 reasoning_content
            reasoning = getattr(msg, "reasoning_content", None)
            if isinstance(reasoning, str) and reasoning.strip():
                return reasoning
    except Exception:
        pass
    # Responses 风格
    try:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text
        output = getattr(response, "output", None)
        if output:
            chunks: List[str] = []
            for item in output:
                content = getattr(item, "content", None)
                if not content:
                    continue
                for c in content:
                    text = getattr(c, "text", None)
                    if isinstance(text, str):
                        chunks.append(text)
            if chunks:
                return "\n".join(chunks)
    except Exception:
        pass
    return None


# ------------------------------------------------------------------
# 对外主入口：商品评估
# ------------------------------------------------------------------
async def analyze_product(
    *,
    title: str,
    raw_text: str = "",
    image_urls: Optional[List[str]] = None,
    image_bytes_list: Optional[List[Tuple[bytes, str]]] = None,
    user_prompt: Optional[str] = None,
    schema_hint: Optional[Dict[str, Any]] = None,
    prefer_fallback: bool = False,
) -> Dict[str, Any]:
    """
    主入口。多模态由主路 (M3) 处理；图片侧失败/降级由 DeepSeek 文本路接管。

    返回结构：
    {
      "decision": "match" | "reject" | "review",
      "confidence": float,
      "reason": str,
      "raw": str,
      "provider": "primary" | "fallback",
      "error": Optional[str],
    }
    """
    schema_hint = schema_hint or {
        "decision": "match|reject|review",
        "confidence": "0..1 float",
        "reason": "short reason",
    }
    user_prompt = user_prompt or "请分析这件商品是否满足我给出的筛选标准。"

    # 构建 user message
    content_blocks: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    content_blocks.append({"type": "text", "text": f"标题: {title}"})
    if raw_text:
        content_blocks.append({"type": "text", "text": f"原文片段: {raw_text[:1500]}"})
    if image_urls:
        for url in image_urls[:6]:
            content_blocks.append({"type": "image_url", "image_url": {"url": url}})
    if image_bytes_list:
        for data, mime in image_bytes_list[:6]:
            content_blocks.append({
                "type": "image_url",
                "image_url": {"url": encode_image_as_data_url(data, mime)},
            })

    system_prompt = (
        "你是一个商品筛选助手，请阅读用户给定的标准并对当前商品做出判断。"
        f"请严格以 JSON 输出，字段：{json.dumps(schema_hint, ensure_ascii=False)}。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content_blocks},
    ]

    # 路由顺序
    if prefer_fallback:
        order = [("fallback", get_fallback_client(), fallback_ai_settings.model_name, False),
                 ("primary", get_primary_client(), ai_settings.model_name, True)]
    else:
        order = [("primary", get_primary_client(), ai_settings.model_name, True),
                 ("fallback", get_fallback_client(), fallback_ai_settings.model_name, False)]

    last_error: Optional[str] = None
    for provider, client, model, has_image in order:
        if client is None:
            last_error = f"{provider}_not_configured"
            continue
        # 备路没有图片能力：剥离 image 块
        msgs = _strip_images(messages) if not has_image else messages
        text, err = await _call_once(
            client, model=model, messages=msgs, enable_json=True, temperature=0.2,
        )
        if err:
            last_error = f"{provider}:{err}"
            log.warning("AI provider %s failed: %s", provider, err)
            continue
        parsed = _safe_parse_json(text or "")
        return {
            "decision": parsed.get("decision", "review"),
            "confidence": float(parsed.get("confidence", 0.0) or 0.0),
            "reason": parsed.get("reason", "") or "",
            "raw": text or "",
            "provider": provider,
            "error": None,
        }

    return {
        "decision": "review",
        "confidence": 0.0,
        "reason": "所有 AI 路径均不可用",
        "raw": "",
        "provider": "none",
        "error": last_error or "no_provider",
    }


def _strip_images(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            new_blocks = [b for b in content if b.get("type") != "image_url"]
            text_only = "\n".join(
                b.get("text", "") for b in new_blocks if b.get("type") == "text"
            )
            out.append({"role": m["role"], "content": text_only or "(无文本)"})
        else:
            out.append(m)
    return out


def _safe_parse_json(text: str) -> Dict[str, Any]:
    """AI 可能把 JSON 包在 ```json ... ``` 里，宽容解析。"""
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        # 尝试从文本里抠出第一段 {...}
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except Exception:
                return {}
    return {}


# ------------------------------------------------------------------
# 自检：阶段 1 验证用
# ------------------------------------------------------------------
async def self_test() -> Dict[str, Any]:
    """阶段 1 验证：仅 ping，不带图片。返回两侧连通状态。"""
    out: Dict[str, Any] = {"primary": None, "fallback": None}
    primary = get_primary_client()
    if primary:
        text, err = await _call_once(
            primary, model=ai_settings.model_name,
            messages=[{"role": "user", "content": "ping, 回复 OK"}],
            enable_json=False, temperature=0.0, timeout=15.0,
        )
        out["primary"] = {"ok": err is None, "text": text, "error": err}
    else:
        out["primary"] = {"ok": False, "error": "not_configured"}
    fallback = get_fallback_client()
    if fallback:
        text, err = await _call_once(
            fallback, model=fallback_ai_settings.model_name,
            messages=[{"role": "user", "content": "ping, 回复 OK"}],
            enable_json=False, temperature=0.0, timeout=15.0,
        )
        out["fallback"] = {"ok": err is None, "text": text, "error": err}
    else:
        out["fallback"] = {"ok": False, "error": "not_configured"}
    return out
