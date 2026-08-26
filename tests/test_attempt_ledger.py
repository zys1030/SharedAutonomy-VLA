"""Attempt ledger: retry counts survive deleting a failed episode directory."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sharedautonomy.data import (
    ATTEMPT_LEDGER_FILENAME,
    ATTEMPT_LEDGER_HEADER,
    AttemptLedgerError,
    AttemptLedgerSession,
    CollectionMode,
    assert_ledger_survives_run_delete,
    default_attempt_ledger_path,
    iter_attempt_events,
    next_attempt_index,
)

pytestmark = pytest.mark.core

_STAMP = datetime(2026, 8, 17, 2, 42, tzinfo=UTC)


def _begin(
    tmp_path: Path,
    *,
    run_id: str = "shape-pick-place-block-rot-manual-001",
    layout_id: str | None = "1",
    collection_mode: CollectionMode = CollectionMode.MANUAL,
    yaw_bin_deg: float | None = 45.0,
    ledger_path: Path | None = None,
) -> AttemptLedgerSession:
    record_dir = tmp_path / run_id / "episode"
    return AttemptLedgerSession.begin(
        record_dir=record_dir,
        run_id=run_id,
        episode_id="episode-test",
        layout_id=layout_id,
        collection_mode=collection_mode,
        ledger_path=ledger_path,
        task_id="shape_pick_place_v1",
        task_text="Pick up the red cube and place it in the UP region.",
        source_object="red",
        destination="up",
        yaw_bin_deg=yaw_bin_deg,
        argv=["python", "scripts/collect_demonstrations.py", "--record-dir", str(record_dir)],
        timestamp_utc=_STAMP,
    )


def test_default_ledger_path_is_sibling_of_run_directory(tmp_path: Path) -> None:
    record_dir = tmp_path / "runs" / "shape-pick-place-block-rot-manual-001" / "episode"
    assert default_attempt_ledger_path(record_dir) == tmp_path / "runs" / ATTEMPT_LEDGER_FILENAME


def test_default_ledger_path_without_episode_dirname(tmp_path: Path) -> None:
    record_dir = tmp_path / "custom-episode"
    assert default_attempt_ledger_path(record_dir) == tmp_path / ATTEMPT_LEDGER_FILENAME


def test_rejects_ledger_inside_run_directory(tmp_path: Path) -> None:
    record_dir = tmp_path / "run-001" / "episode"
    inside = tmp_path / "run-001" / "attempts.jsonl"
    with pytest.raises(AttemptLedgerError, match="inside run directory"):
        assert_ledger_survives_run_delete(inside, record_dir)


def test_begin_writes_start_and_increments_on_retry(tmp_path: Path) -> None:
    first = _begin(tmp_path)
    assert first.attempt_index == 1
    assert first.ledger_path == tmp_path / ATTEMPT_LEDGER_FILENAME
    second = _begin(tmp_path, run_id="shape-pick-place-block-rot-manual-001-retry")
    assert second.attempt_index == 2
    assert second.attempt_id != first.attempt_id
    events = list(iter_attempt_events(first.ledger_path))
    assert [event["event"] for event in events] == ["start", "start"]
    assert [event["attempt_index"] for event in events] == [1, 2]
    assert events[0]["layout_id"] == "1"
    assert events[0]["yaw_bin_deg"] == 45.0
    assert events[0]["argv"][-1].endswith("episode")


def test_retry_count_survives_deleted_episode_dir(tmp_path: Path) -> None:
    first = _begin(tmp_path)
    episode_dir = Path(first.record_dir)
    episode_dir.mkdir(parents=True)
    (episode_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (episode_dir / "metadata.json").unlink()
    episode_dir.rmdir()
    episode_dir.parent.rmdir()
    assert not episode_dir.parent.exists()
    assert first.ledger_path.is_file()
    second = _begin(tmp_path)
    assert second.attempt_index == 2


def test_begin_refuses_ledger_inside_run_directory(tmp_path: Path) -> None:
    record_dir = tmp_path / "run-001" / "episode"
    with pytest.raises(AttemptLedgerError, match="inside run directory"):
        AttemptLedgerSession.begin(
            record_dir=record_dir,
            run_id="run-001",
            episode_id="episode-test",
            ledger_path=tmp_path / "run-001" / "attempts.jsonl",
        )


def test_manual_and_shared_autonomy_counts_are_independent(tmp_path: Path) -> None:
    manual = _begin(tmp_path)
    sa = _begin(
        tmp_path,
        run_id="shape-pick-place-block-rot-sa-001",
        collection_mode=CollectionMode.SHARED_AUTONOMY,
    )
    assert manual.attempt_index == 1
    assert sa.attempt_index == 1
    assert next_attempt_index(manual.ledger_path, layout_id="1", collection_mode=CollectionMode.MANUAL) == 2


def test_finish_is_idempotent_and_records_status(tmp_path: Path) -> None:
    session = _begin(tmp_path)
    payload = session.finish(
        recording_status="completed",
        episode_success=True,
        step_count=12,
        end_trigger="gripper_release",
        timestamp_utc=_STAMP,
    )
    assert payload is not None
    assert payload["recording_status"] == "completed"
    assert session.finish(recording_status="aborted") is None
    events = list(iter_attempt_events(session.ledger_path))
    assert [event["event"] for event in events] == ["start", "finish"]


def test_invalid_recording_status_rejected(tmp_path: Path) -> None:
    session = _begin(tmp_path)
    with pytest.raises(AttemptLedgerError, match="recording_status"):
        session.finish(recording_status="deleted_fail")


def test_new_ledger_contains_copy_paste_failure_templates(tmp_path: Path) -> None:
    session = _begin(tmp_path)
    text = session.ledger_path.read_text(encoding="utf-8")
    assert '# {"event":"outcome","task_outcome":"pinch_edge"}' in text
    assert '{"event":"outcome","task_outcome":"pinch_edge"}' in ATTEMPT_LEDGER_HEADER
    events = list(iter_attempt_events(session.ledger_path))
    assert [event["event"] for event in events] == ["start"]


def test_pasted_outcome_line_does_not_change_attempt_index(tmp_path: Path) -> None:
    first = _begin(tmp_path)
    with first.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"event":"outcome","task_outcome":"pinch_edge"}\n')
    events = list(iter_attempt_events(first.ledger_path))
    assert [event["event"] for event in events] == ["start", "outcome"]
    second = _begin(tmp_path, run_id="shape-pick-place-block-rot-manual-001-retry")
    assert second.attempt_index == 2
