"""Standard task_text strings for shape_pick_place_v1 (see docs/tasks/shape_pick_place_v1.md)."""

from __future__ import annotations

_DEFAULT_TELEOP_TASK_TEXT = "Manual Cartesian teleop smoke recording."

# object_id -> destination_id -> standard English (must match task card §3 exactly).
SHAPE_PICK_PLACE_V1_TASK_TEXTS: dict[tuple[str, str], str] = {
    ("yellow", "up"): "Pick up the yellow triangle and place it in the UP region.",
    ("yellow", "down"): "Pick up the yellow triangle and place it in the DOWN region.",
    ("red", "up"): "Pick up the red circle and place it in the UP region.",
    ("red", "down"): "Pick up the red circle and place it in the DOWN region.",
    ("blue", "up"): "Pick up the blue rectangle and place it in the UP region.",
    ("blue", "down"): "Pick up the blue rectangle and place it in the DOWN region.",
}


def _normalize_id(value: str, *, field_name: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def task_text_for_shape_pick_place(source_object: str, destination: str) -> str:
    """Return the standard task_text for a (color, region) pair."""
    object_id = _normalize_id(source_object, field_name="source_object")
    destination_id = _normalize_id(destination, field_name="destination")
    try:
        return SHAPE_PICK_PLACE_V1_TASK_TEXTS[(object_id, destination_id)]
    except KeyError as exc:
        valid_objects = sorted({key[0] for key in SHAPE_PICK_PLACE_V1_TASK_TEXTS})
        valid_destinations = sorted({key[1] for key in SHAPE_PICK_PLACE_V1_TASK_TEXTS})
        raise ValueError(
            f"Unknown shape_pick_place_v1 condition ({object_id!r}, {destination_id!r}). "
            f"Valid object_id: {valid_objects}; valid destination_id: {valid_destinations}."
        ) from exc


def resolve_episode_task_text(
    *,
    task_text: str | None,
    source_object: str | None,
    destination: str | None,
) -> str:
    """Resolve metadata task_text for teleop recording.

    Explicit ``task_text`` wins. Otherwise, when both ``source_object`` and
    ``destination`` are set, use the shape_pick_place_v1 standard sentence.
    """
    if task_text is not None:
        stripped = task_text.strip()
        if stripped:
            return stripped
    if source_object is not None and destination is not None:
        return task_text_for_shape_pick_place(source_object, destination)
    return _DEFAULT_TELEOP_TASK_TEXT
