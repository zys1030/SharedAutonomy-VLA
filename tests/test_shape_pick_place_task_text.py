import pytest

from sharedautonomy.tasks.shape_pick_place_v1 import (
    resolve_episode_task_text,
    task_text_for_shape_pick_place,
)


@pytest.mark.core
def test_task_text_for_shape_pick_place_red_up() -> None:
    assert (
        task_text_for_shape_pick_place("red", "up")
        == "Pick up the red circle and place it in the UP region."
    )


@pytest.mark.core
def test_task_text_for_shape_pick_place_normalizes_case() -> None:
    assert (
        task_text_for_shape_pick_place(" Blue ", "DOWN")
        == "Pick up the blue rectangle and place it in the DOWN region."
    )


@pytest.mark.core
def test_resolve_episode_task_text_auto_from_ids() -> None:
    assert (
        resolve_episode_task_text(
            task_text=None,
            source_object="yellow",
            destination="down",
        )
        == "Pick up the yellow triangle and place it in the DOWN region."
    )


@pytest.mark.core
def test_resolve_episode_task_text_explicit_override() -> None:
    custom = "Place the yellow triangle in the upper area."
    assert (
        resolve_episode_task_text(
            task_text=custom,
            source_object="yellow",
            destination="up",
        )
        == custom
    )


@pytest.mark.core
def test_task_text_for_shape_pick_place_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown shape_pick_place_v1"):
        task_text_for_shape_pick_place("green", "up")
