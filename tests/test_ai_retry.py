"""Tests for P0 #4 — AI client retry on empty responses.

The router retries up to 4 times when ``_extract_text`` returns ``None``
(empty AI response) before giving up with ``ai_returned_empty``. Existing
behaviors — temperature-unsupported retry, /responses vs /chat/completions
fallback — must continue to work.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from cd_monitor.infrastructure.external import ai_client
from cd_monitor.infrastructure.external.ai_client import (
    EMPTY_MAX_RETRIES,
    _call_once,
)
from cd_monitor.infrastructure.external.ai_request_compat import (
    CHAT_COMPLETIONS_API_MODE,
    RESPONSES_API_MODE,
    is_chat_completions_api_unsupported_error,
    is_responses_api_unsupported_error,
    is_temperature_unsupported_error,
)


class _StubClient:
    """Minimal client object — real client fields are not touched because
    ``create_ai_response_async`` is patched out in every test."""

    pass


class _TempUnsupportedError(Exception):
    pass


class _ResponsesUnsupportedError(Exception):
    pass


class _ChatUnsupportedError(Exception):
    pass


# These three error classes are matched by the compatibility helpers.
# If a real helper signature changes we update the markers here too.
for _cls, _err in (
    (_TempUnsupportedError, "Unsupported value: 'temperature'"),
    (_ResponsesUnsupportedError, "404 not found"),
    (_ChatUnsupportedError, "404 not found"),
):
    try:
        # The helpers discriminate by exception instance or message text.
        # Register the class globally so the helpers can identify them.
        globals()[_cls.__name__] = _cls
    except Exception:
        pass


def _patched_client(monkeypatch: pytest.MonkeyPatch,
                    side_effects: list[Any]) -> dict[str, int]:
    """Patch create_ai_response_async and return a counter dict."""
    call_counter = {"n": 0}

    async def _stub(*_args: Any, **_kwargs: Any) -> Any:
        idx = call_counter["n"]
        call_counter["n"] += 1
        if idx < len(side_effects):
            item = side_effects[idx]
            if isinstance(item, BaseException):
                raise item
            return item
        return None

    monkeypatch.setattr(ai_client, "create_ai_response_async", _stub)
    return call_counter


def _patched_extract(monkeypatch: pytest.MonkeyPatch,
                     outputs: list[Any]) -> dict[str, int]:
    """Patch _extract_text to return scripted outputs."""
    call_counter = {"n": 0}

    def _stub(_response: Any) -> Any:
        idx = call_counter["n"]
        call_counter["n"] += 1
        if idx < len(outputs):
            return outputs[idx]
        return None

    monkeypatch.setattr(ai_client, "_extract_text", _stub)
    return call_counter


@pytest.mark.asyncio
async def test_success_first_try_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _StubClient()
    n_client = _patched_client(monkeypatch, ["hello"])
    n_extract = _patched_extract(monkeypatch, ["hi there"])

    text, err = await _call_once(
        client, model="x", messages=[{"role": "user", "content": "yo"}],
        max_empty_retries=4,
    )
    assert err is None
    assert text == "hi there"
    assert n_client["n"] == 1
    assert n_extract["n"] == 1


@pytest.mark.asyncio
async def test_empty_response_retries_until_text(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _StubClient()
    n_client = _patched_client(monkeypatch, [None, None, "third-call-payload"])
    # _extract_text: empty twice, then non-empty once.
    n_extract = _patched_extract(monkeypatch, [None, None, {"ok": True}])

    text, err = await _call_once(
        client, model="x", messages=[{"role": "user", "content": "yo"}],
    )
    assert err is None
    assert text == {"ok": True}
    # Three attempts total — two empty retries + the final success.
    assert n_client["n"] == 3
    assert n_extract["n"] == 3


@pytest.mark.asyncio
async def test_empty_response_gives_up_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StubClient()
    # Empty forever — retry budget caps it.
    n_client = _patched_client(monkeypatch, [None] * (EMPTY_MAX_RETRIES + 5))
    n_extract = _patched_extract(monkeypatch, [None] * 10)

    text, err = await _call_once(
        client, model="x", messages=[{"role": "user", "content": "yo"}],
    )
    assert text is None
    assert err == "ai_returned_empty"
    # Max retries = EMPTY_MAX_RETRIES: 4 retries on top of the initial call
    # would be 5 calls; the budget caps it at exactly EMPTY_MAX_RETRIES+1.
    assert n_client["n"] == EMPTY_MAX_RETRIES + 1
    assert n_extract["n"] == EMPTY_MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_max_empty_retries_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _StubClient()
    n_client = _patched_client(monkeypatch, [None] * 10)
    n_extract = _patched_extract(monkeypatch, [None] * 10)

    text, err = await _call_once(
        client, model="x", messages=[{"role": "user", "content": "yo"}],
        max_empty_retries=2,
    )
    assert text is None
    assert err == "ai_returned_empty"
    assert n_client["n"] == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_temperature_unsupported_retry_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StubClient()
    # The compat helpers use message text matching, not class identity.
    # Patch them directly so we control what they decide is unsupported.
    monkeypatch.setattr(
        ai_client, "is_temperature_unsupported_error",
        lambda exc: "temperature" in str(exc).lower(),
    )
    n_client = _patched_client(
        monkeypatch,
        [RuntimeError("temperature not supported"), "ok-payload"],
    )
    n_extract = _patched_extract(monkeypatch, ["final-text"])

    text, err = await _call_once(
        client, model="x", messages=[{"role": "user", "content": "yo"}],
    )
    assert err is None
    assert text == "final-text"
    # Two calls: first hit temperature error, second succeeded after
    # the helper removed temperature.
    assert n_client["n"] == 2


@pytest.mark.asyncio
async def test_responses_to_chat_completions_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StubClient()
    # First call: /responses 404 → switch to /chat/completions.
    # Second call: succeeds.
    monkeypatch.setattr(
        ai_client, "is_responses_api_unsupported_error",
        lambda exc: "responses" in str(exc).lower() and "404" in str(exc),
    )
    monkeypatch.setattr(
        ai_client, "is_chat_completions_api_unsupported_error",
        lambda exc: False,
    )
    n_client = _patched_client(
        monkeypatch,
        [RuntimeError("responses 404 not found"), "ok-payload"],
    )
    n_extract = _patched_extract(monkeypatch, ["final-text"])

    text, err = await _call_once(
        client, model="x", messages=[{"role": "user", "content": "yo"}],
    )
    assert err is None
    assert text == "final-text"
    assert n_client["n"] == 2


@pytest.mark.asyncio
async def test_chat_completions_unsupported_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StubClient()
    # First call hits /responses 404 -> switch to /chat/completions.
    # Second call hits /chat/completions 404 -> return api_not_supported.
    monkeypatch.setattr(
        ai_client, "is_responses_api_unsupported_error",
        lambda exc: "responses" in str(exc).lower(),
    )
    monkeypatch.setattr(
        ai_client, "is_chat_completions_api_unsupported_error",
        lambda exc: "chat" in str(exc).lower(),
    )
    n_client = _patched_client(
        monkeypatch,
        [RuntimeError("responses 404"), RuntimeError("chat 404")],
    )

    text, err = await _call_once(
        client, model="x", messages=[{"role": "user", "content": "yo"}],
    )
    assert text is None
    assert err is not None
    assert "api_not_supported" in err
    # Two calls: one responses attempt, one chat attempt.
    assert n_client["n"] == 2


@pytest.mark.asyncio
async def test_timeout_returns_ai_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timeouts are NOT retried; they bubble up immediately."""
    import asyncio

    client = _StubClient()

    async def _timeout(*_args: Any, **_kwargs: Any) -> Any:
        raise asyncio.TimeoutError()

    monkeypatch.setattr(ai_client, "create_ai_response_async", _timeout)
    text, err = await _call_once(
        client, model="x", messages=[{"role": "user", "content": "yo"}],
    )
    assert text is None
    assert err == "ai_timeout"
