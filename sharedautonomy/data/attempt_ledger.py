"""Append-only attempt ledger that survives deleting a failed episode directory.

Every ``--record-dir`` session must write here *before* the episode recorder
starts. The default path is a sibling of the run directory (for the usual
``outputs/runs/<run_id>/episode`` layout that is ``outputs/runs/attempts.jsonl``),
so removing ``<run_id>/`` still leaves start/finish rows for retry counts.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sharedautonomy.data.schema import CollectionMode

ATTEMPT_LEDGER_FILENAME = "attempts.jsonl"
ATTEMPT_LEDGER_FORMAT = "attempt_ledger.v1"
START_EVENT = "start"
FINISH_EVENT = "finish"
OUTCOME_EVENT = "outcome"
TASK_OUTCOME_PINCH_EDGE = "pinch_edge"
TASK_OUTCOME_SLIP = "slip"
TASK_OUTCOME_KNOCK_OFF = "knock_off"
TASK_OUTCOME_MISSED_UP = "missed_up"
# Operator copy-paste lines. Written as # comments so they are not events.
ATTEMPT_LEDGER_HEADER = """\
# Attempt ledger (JSONL). Blank lines and # comments are ignored.
# Failed take: copy ONE JSON line to the END of this file. Strip the leading "# ".
# Do not edit the JSON. It applies to the latest start. Then you may delete the episode dir.
# 失败：复制下面某一行到文件末尾，去掉开头的 # 和空格，不要改内容。
# {"event":"outcome","task_outcome":"pinch_edge"}
# {"event":"outcome","task_outcome":"slip"}
# {"event":"outcome","task_outcome":"knock_off"}
# {"event":"outcome","task_outcome":"missed_up"}
"""
RECORDING_STATUS_COMPLETED = "completed"
RECORDING_STATUS_ABORTED = "aborted"
RECORDING_STATUS_INTERRUPTED = "interrupted"
RECORDING_STATUS_ERROR = "error"
_RECORDING_STATUSES = frozenset(
    {
        RECORDING_STATUS_COMPLETED,
        RECORDING_STATUS_ABORTED,
        RECORDING_STATUS_INTERRUPTED,
        RECORDING_STATUS_ERROR,
    }
)


class AttemptLedgerError(RuntimeError):
    """Raised when the attempt ledger cannot be written or is misconfigured."""


def default_attempt_ledger_path(record_dir: str | Path) -> Path:
    """Return a ledger path that is *outside* the run directory.

    ``outputs/runs/<run_id>/episode`` → ``outputs/runs/attempts.jsonl``.
    Any other episode path → ``<parent>/attempts.jsonl``.
    """
    episode_dir = Path(record_dir).resolve()
    if episode_dir.name == "episode":
        return episode_dir.parent.parent / ATTEMPT_LEDGER_FILENAME
    return episode_dir.parent / ATTEMPT_LEDGER_FILENAME


def assert_ledger_survives_run_delete(ledger_path: str | Path, record_dir: str | Path) -> None:
    """Refuse a ledger that would be deleted together with the episode run."""
    episode_dir = Path(record_dir).resolve()
    run_dir = episode_dir.parent if episode_dir.name == "episode" else episode_dir
    ledger = Path(ledger_path).resolve()
    try:
        ledger.relative_to(run_dir)
    except ValueError:
        return
    raise AttemptLedgerError(
        f"attempt ledger {ledger} is inside run directory {run_dir}; "
        "deleting a failed episode would also delete the retry log. "
        f"Use {default_attempt_ledger_path(episode_dir)}"
    )


def iter_attempt_events(ledger_path: str | Path) -> Iterable[dict[str, Any]]:
    """Yield parsed JSONL events. Missing files, blanks, and ``#`` comments yield nothing."""
    path = Path(ledger_path)
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AttemptLedgerError(f"corrupt attempt ledger {path} line {line_number}: {exc}") from exc
            if not isinstance(payload, dict):
                raise AttemptLedgerError(f"corrupt attempt ledger {path} line {line_number}: expected object")
            yield payload


def next_attempt_index(
    ledger_path: str | Path,
    *,
    layout_id: str,
    collection_mode: str | CollectionMode,
) -> int:
    """Return the 1-based attempt number for this layout and collection mode."""
    mode = str(CollectionMode(collection_mode))
    starts = 0
    for event in iter_attempt_events(ledger_path):
        if event.get("event") != START_EVENT:
            continue
        if str(event.get("layout_id", "")) != layout_id:
            continue
        if str(event.get("collection_mode", "")) != mode:
            continue
        starts += 1
    return starts + 1


def _ledger_has_copy_templates(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    return '# {"event":"outcome","task_outcome":"pinch_edge"}' in path.read_text(encoding="utf-8")


def ensure_attempt_ledger_header(ledger_path: str | Path) -> None:
    """Write copy-paste failure templates if this ledger does not already have them."""
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _ledger_has_copy_templates(path):
        return
    append_to_existing = path.is_file() and path.stat().st_size > 0
    try:
        with path.open("a" if append_to_existing else "w", encoding="utf-8", newline="\n") as handle:
            if append_to_existing:
                handle.write("\n")
            handle.write(ATTEMPT_LEDGER_HEADER)
            if not ATTEMPT_LEDGER_HEADER.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise AttemptLedgerError(f"failed to write attempt ledger header {path}: {exc}") from exc


def append_attempt_event(ledger_path: str | Path, event: Mapping[str, Any]) -> dict[str, Any]:
    """Append one JSON object as a JSONL row and fsync it to disk."""
    path = Path(ledger_path)
    payload = dict(event)
    ensure_attempt_ledger_header(path)
    try:
        serialized = json.dumps(payload, ensure_ascii=True, default=str)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise AttemptLedgerError(f"failed to append attempt ledger {path}: {exc}") from exc
    return payload


def _require_non_empty(value: str, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise AttemptLedgerError(f"{name} must be a non-empty string")
    return text


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _timestamp_utc(value: datetime | None) -> str:
    stamp = value or _utc_now()
    if stamp.tzinfo is None:
        raise AttemptLedgerError("timestamp_utc must be timezone-aware")
    return stamp.astimezone(UTC).isoformat()


@dataclass(slots=True)
class AttemptLedgerSession:
    """One recording attempt: start row on begin, finish row on close."""

    ledger_path: Path
    attempt_id: str
    layout_id: str
    attempt_index: int
    record_dir: str
    collection_mode: str
    run_id: str
    episode_id: str
    _finished: bool = field(default=False, init=False, repr=False)

    @classmethod
    def begin(
        cls,
        *,
        record_dir: str | Path,
        run_id: str,
        episode_id: str,
        layout_id: str | None = None,
        collection_mode: str | CollectionMode = CollectionMode.MANUAL,
        ledger_path: str | Path | None = None,
        task_id: str | None = None,
        task_text: str | None = None,
        source_object: str | None = None,
        destination: str | None = None,
        yaw_bin_deg: float | None = None,
        argv: Iterable[str] | None = None,
        git_commit: str | None = None,
        timestamp_utc: datetime | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> AttemptLedgerSession:
        """Write the start event. Recording must not begin if this raises."""
        episode_dir = Path(record_dir)
        resolved_ledger = (
            Path(ledger_path) if ledger_path is not None else default_attempt_ledger_path(episode_dir)
        )
        assert_ledger_survives_run_delete(resolved_ledger, episode_dir)
        mode = str(CollectionMode(collection_mode))
        resolved_run_id = _require_non_empty(run_id, "run_id")
        resolved_episode_id = _require_non_empty(episode_id, "episode_id")
        resolved_layout_id = _require_non_empty(
            layout_id if layout_id is not None else resolved_run_id,
            "layout_id",
        )
        if yaw_bin_deg is not None:
            angle = float(yaw_bin_deg)
            if not math.isfinite(angle):
                raise AttemptLedgerError("yaw_bin_deg must be finite")
            yaw_bin_deg = angle
        attempt_index = next_attempt_index(
            resolved_ledger,
            layout_id=resolved_layout_id,
            collection_mode=mode,
        )
        session = cls(
            ledger_path=resolved_ledger,
            attempt_id=uuid.uuid4().hex,
            layout_id=resolved_layout_id,
            attempt_index=attempt_index,
            record_dir=str(episode_dir),
            collection_mode=mode,
            run_id=resolved_run_id,
            episode_id=resolved_episode_id,
        )
        event: dict[str, Any] = {
            "format": ATTEMPT_LEDGER_FORMAT,
            "event": START_EVENT,
            "attempt_id": session.attempt_id,
            "attempt_index": session.attempt_index,
            "layout_id": session.layout_id,
            "collection_mode": session.collection_mode,
            "run_id": session.run_id,
            "episode_id": session.episode_id,
            "record_dir": session.record_dir,
            "timestamp_utc": _timestamp_utc(timestamp_utc),
            "task_id": task_id,
            "task_text": task_text,
            "source_object": source_object,
            "destination": destination,
            "yaw_bin_deg": yaw_bin_deg,
            "argv": [str(item) for item in argv] if argv is not None else None,
            "git_commit": git_commit,
        }
        if extra:
            event.update(dict(extra))
        append_attempt_event(resolved_ledger, event)
        return session

    def finish(
        self,
        *,
        recording_status: str,
        episode_success: bool | None = None,
        failure_reason: str | None = None,
        step_count: int | None = None,
        end_trigger: str | None = None,
        timestamp_utc: datetime | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Write the finish event once. Later calls are no-ops."""
        if self._finished:
            return None
        status = str(recording_status).strip()
        if status not in _RECORDING_STATUSES:
            raise AttemptLedgerError(
                f"recording_status must be one of {sorted(_RECORDING_STATUSES)}; got {recording_status!r}"
            )
        event: dict[str, Any] = {
            "format": ATTEMPT_LEDGER_FORMAT,
            "event": FINISH_EVENT,
            "attempt_id": self.attempt_id,
            "attempt_index": self.attempt_index,
            "layout_id": self.layout_id,
            "collection_mode": self.collection_mode,
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "record_dir": self.record_dir,
            "timestamp_utc": _timestamp_utc(timestamp_utc),
            "recording_status": status,
            "episode_success": episode_success,
            "failure_reason": failure_reason,
            "step_count": step_count,
            "end_trigger": end_trigger,
        }
        if extra:
            event.update(dict(extra))
        payload = append_attempt_event(self.ledger_path, event)
        self._finished = True
        return payload

    @property
    def finished(self) -> bool:
        return self._finished
