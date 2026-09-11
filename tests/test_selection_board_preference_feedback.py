import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _brace_block(source: str, start: int) -> str:
    opening = source.index("{", start)
    depth = 0
    for position in range(opening, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[start : position + 1]
    raise AssertionError("unterminated block")


def _function_block(source: str, name: str) -> str:
    match = re.search(
        rf"^  (?:async )?function {re.escape(name)}\([^)]*\) \{{",
        source,
        flags=re.MULTILINE,
    )
    assert match, f"missing function: {name}"
    return _brace_block(source, match.start())


def test_selection_feedback_controls_are_candidate_scoped_and_reference_profiles_stay_read_only() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "discovery-ui.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "styles" / "kuro.css").read_text(encoding="utf-8")

    assert 'id="selectionFeedbackState"' in html
    assert "selectionFeedbackStatus: null" in script
    assert "selectionFeedback: []" in script

    refresh_board = _function_block(script, "refreshBoard")
    assert 'apiGet("/api/selection-feedback/status").catch(() => null)' in refresh_board
    assert 'apiGet("/api/selection-feedback?current=1&limit=200").catch(() => null)' in refresh_board
    assert "view.selectionFeedbackStatus = selectionFeedbackStatus;" in refresh_board
    assert "view.selectionFeedback = (selectionFeedbackPayload && selectionFeedbackPayload.items) || [];" in refresh_board

    opportunity_card = _function_block(script, "opportunityCard")
    assert "Number.isSafeInteger(candidateId) && candidateId > 0" in opportunity_card
    assert 'data-selection-feedback="keep"' in opportunity_card
    assert 'data-selection-feedback="source_pending"' in opportunity_card
    assert 'data-selection-feedback="not_fit"' in opportunity_card
    assert 'data-candidate-id="' in opportunity_card
    assert "esc(candidateId)" in opportunity_card

    reference_renderer = _function_block(script, "renderReferenceMemory")
    assert "data-selection-feedback" not in reference_renderer
    assert "selectionFeedback" not in reference_renderer

    controls = _function_block(script, "bindControls")
    assert 'apiPost("/api/selection-feedback"' in controls
    assert "candidate_id: Number(button.dataset.candidateId)" in controls
    assert "outcome: button.dataset.selectionFeedback" in controls
    assert "refreshBoard();" in controls
    assert "自动" not in controls
    assert "购买" not in controls
    assert "淘汰" not in controls

    assert "偏好反馈暂不可用" in script
    assert "body.kuro .selection-feedback-actions" in styles
