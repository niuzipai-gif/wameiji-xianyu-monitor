from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from cd_monitor.core.discovery import DiscoveryCandidate
from cd_monitor.storage.sqlite import (
    insert_selection_preference_feedback,
    list_discovery_pools,
    list_selection_preference_feedback,
    selection_preference_feedback_status,
    upsert_discovery_candidate,
)
from cd_monitor.web_server import create_server


def _candidate_id(db_path, suffix: str) -> int:
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    return upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key=f"selection-preference:{suffix}",
            title=f"Selection preference {suffix}",
            availability="available",
        ),
    )


def _get_json_with_status(url: str) -> tuple[int, dict[str, object]]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _post_json_with_status(url: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def test_selection_preference_feedback_is_append_only_and_latest_per_candidate(tmp_path) -> None:
    db_path = tmp_path / "selection-preference.db"
    candidate_id = _candidate_id(db_path, "append-only")

    first = insert_selection_preference_feedback(
        db_path,
        candidate_id=candidate_id,
        outcome="source_pending",
        note="产品方向保留，暂时找不到便宜货",
    )
    second = insert_selection_preference_feedback(
        db_path,
        candidate_id=candidate_id,
        outcome="not_fit",
        note="产品方向不符合",
    )
    first_id = first["id"]
    second_id = second["id"]

    assert second_id > first_id
    assert first == {
        "id": first_id,
        "candidate_id": candidate_id,
        "outcome": "source_pending",
        "note": "产品方向保留，暂时找不到便宜货",
        "created_at": first["created_at"],
    }
    history = list_selection_preference_feedback(db_path, candidate_id=candidate_id)
    assert [(row["id"], row["outcome"], row["note"]) for row in history] == [
        (second_id, "not_fit", "产品方向不符合"),
        (first_id, "source_pending", "产品方向保留，暂时找不到便宜货"),
    ]
    assert all(row["candidate_id"] == candidate_id for row in history)
    assert all(row["created_at"] for row in history)
    current = list_selection_preference_feedback(
        db_path,
        candidate_id=candidate_id,
        current_only=True,
    )
    assert [(row["id"], row["outcome"]) for row in current] == [(second_id, "not_fit")]


def test_selection_preference_feedback_rejects_unknown_candidates_and_outcomes(tmp_path) -> None:
    db_path = tmp_path / "selection-preference.db"

    with pytest.raises(KeyError, match="Discovery candidate not found"):
        insert_selection_preference_feedback(
            db_path,
            candidate_id=999,
            outcome="keep",
        )
    with pytest.raises(ValueError, match="Unsupported selection preference outcome"):
        insert_selection_preference_feedback(
            db_path,
            candidate_id=_candidate_id(db_path, "invalid-outcome"),
            outcome="source_is_cheap",
        )
    candidate_id = _candidate_id(db_path, "invalid-note")
    with pytest.raises(ValueError, match="Selection preference note must be text"):
        insert_selection_preference_feedback(
            db_path,
            candidate_id=candidate_id,
            outcome="keep",
            note={"not": "text"},
        )
    with pytest.raises(ValueError, match="Selection preference note is too long"):
        insert_selection_preference_feedback(
            db_path,
            candidate_id=candidate_id,
            outcome="keep",
            note="x" * 501,
        )
    with pytest.raises(ValueError, match="Selection preference candidate ID must be positive"):
        insert_selection_preference_feedback(
            db_path,
            candidate_id=0,
            outcome="keep",
        )
    with pytest.raises(ValueError, match="Selection preference feedback limit must be between 1 and 200"):
        list_selection_preference_feedback(db_path, limit=0)


def test_selection_preference_status_waits_for_balanced_candidate_feedback(tmp_path) -> None:
    db_path = tmp_path / "selection-preference.db"
    for index in range(30):
        candidate_id = _candidate_id(db_path, str(index))
        outcome = "keep" if index < 10 else "source_pending" if index < 20 else "not_fit"
        insert_selection_preference_feedback(db_path, candidate_id=candidate_id, outcome=outcome)

    status = selection_preference_feedback_status(db_path)
    assert status["event_count"] == 30
    assert status["labeled_candidate_count"] == 30
    assert status["current_outcomes"] == {"keep": 10, "source_pending": 10, "not_fit": 10}
    assert status["state"] == "ready_for_evaluation"
    assert status["ready_for_evaluation"] is True


def test_selection_preference_status_separates_all_events_from_current_labels(tmp_path) -> None:
    db_path = tmp_path / "selection-preference-status.db"
    candidate_id = _candidate_id(db_path, "status-history")
    insert_selection_preference_feedback(db_path, candidate_id=candidate_id, outcome="keep")
    insert_selection_preference_feedback(
        db_path,
        candidate_id=candidate_id,
        outcome="source_pending",
    )

    status = selection_preference_feedback_status(db_path)

    assert status["event_count"] == 2
    assert status["labeled_candidate_count"] == 1
    assert status["current_outcomes"] == {
        "keep": 0,
        "source_pending": 1,
        "not_fit": 0,
    }
    assert status["state"] == "collecting_feedback"


def test_selection_preference_feedback_api_records_candidate_direction_only(tmp_path) -> None:
    db_path = tmp_path / "selection-preference-api.db"
    candidate_id = _candidate_id(db_path, "api")
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        initial_status, initial = _get_json_with_status(
            f"{base_url}/api/selection-feedback/status"
        )
        created_status, created = _post_json_with_status(
            f"{base_url}/api/selection-feedback",
            {
                "candidate_id": candidate_id,
                "outcome": "source_pending",
                "note": "产品方向符合，暂时没有便宜货",
            },
        )
        status_status, status = _get_json_with_status(
            f"{base_url}/api/selection-feedback/status"
        )
        listed_status, listed = _get_json_with_status(
            f"{base_url}/api/selection-feedback?candidate_id={candidate_id}&current=1&limit=20"
        )
        invalid_status, invalid = _post_json_with_status(
            f"{base_url}/api/selection-feedback",
            {"candidate_id": candidate_id, "outcome": "cheap_source"},
        )
        missing_status, missing = _post_json_with_status(
            f"{base_url}/api/selection-feedback",
            {"candidate_id": 999, "outcome": "keep"},
        )

        assert initial_status == 200
        assert initial["state"] == "collecting_feedback"
        assert created_status == 201
        assert created["id"] > 0
        assert created["candidate_id"] == candidate_id
        assert created["outcome"] == "source_pending"
        assert created["note"] == "产品方向符合，暂时没有便宜货"
        assert created["created_at"]
        assert status_status == 200
        assert status["current_outcomes"]["source_pending"] == 1
        assert listed_status == 200
        assert listed["items"][0]["candidate_id"] == candidate_id
        assert listed["items"][0]["outcome"] == "source_pending"
        assert invalid_status == 400
        assert invalid == {"error": "invalid_selection_feedback"}
        assert missing_status == 404
        assert missing == {"error": "candidate_not_found"}
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/selection-feedback?limit=1.5", None),
        ("/api/selection-feedback?limit=201", None),
        ("/api/selection-feedback?current=yes", None),
        ("/api/selection-feedback?candidate_id=0", None),
        ("/api/selection-feedback", {"candidate_id": 0, "outcome": "keep"}),
        ("/api/selection-feedback", {"candidate_id": -1, "outcome": "keep"}),
        ("/api/selection-feedback", {"candidate_id": "not-an-id", "outcome": "keep"}),
        ("/api/selection-feedback", {"candidate_id": True, "outcome": "keep"}),
        ("/api/selection-feedback", {"candidate_id": 1, "outcome": "wrong"}),
        ("/api/selection-feedback", {"candidate_id": 1, "outcome": "keep", "note": "x" * 501}),
    ],
)
def test_selection_preference_feedback_api_rejects_invalid_shapes(tmp_path, path, payload) -> None:
    db_path = tmp_path / "selection-preference-api-invalid.db"
    _candidate_id(db_path, "api-invalid")
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        if payload is None:
            status, response = _get_json_with_status(base_url + path)
        else:
            status, response = _post_json_with_status(base_url + path, payload)

        assert status == 400
        assert response == {"error": "invalid_selection_feedback"}
    finally:
        server.shutdown()
        server.server_close()
