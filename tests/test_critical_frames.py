"""Tests for LeRobot grasp-critical frame indexing and sampling."""

from __future__ import annotations

import numpy as np
import pytest
from sharedautonomy.data.critical_frames import (
    CriticalFrameConfig,
    CriticalFrameIndexError,
    EpisodeFrameRange,
    build_critical_frame_index,
    extract_gripper_open_fraction,
    find_close_transitions,
    load_critical_frame_index,
    save_critical_frame_index,
)

pytestmark = pytest.mark.core


def _action(gripper_open_fraction: float) -> np.ndarray:
    values = np.zeros(7, dtype=np.float32)
    values[6] = gripper_open_fraction
    return values


def test_find_close_transitions_uses_open_to_close_crossings() -> None:
    values = [0.8, 0.8, 0.4, 0.4, 0.7, 0.3]

    assert find_close_transitions(values) == (2, 5)


def test_extract_gripper_open_fraction_uses_first_chunk_action() -> None:
    action_chunk = np.zeros((3, 7), dtype=np.float32)
    action_chunk[0, 6] = 0.42
    action_chunk[1, 6] = 0.12

    assert extract_gripper_open_fraction(action_chunk) == pytest.approx(0.42)


def test_build_index_clamps_window_to_episode_and_rejects_multiple_closes() -> None:
    actions = [_action(value) for value in [0.8, 0.8, 0.8, 0.4, 0.4, 0.4]]
    episode_ranges = (EpisodeFrameRange(episode_index=0, from_index=0, to_index=6),)
    index = build_critical_frame_index(
        dataset_repo_id="local/test",
        dataset_root="dataset",
        num_frames=6,
        episode_ranges=episode_ranges,
        action_reader=actions.__getitem__,
        config=CriticalFrameConfig(pre_frames=20, post_frames=10),
    )

    episode = index.episodes[0]
    assert episode.close_frame_index == 3
    assert episode.window_start_index == 0
    assert episode.window_end_index == 5
    assert index.num_window_frames == 6

    multiple_close_actions = [_action(value) for value in [0.8, 0.4, 0.8, 0.4]]
    with pytest.raises(CriticalFrameIndexError, match="multiple"):
        build_critical_frame_index(
            dataset_repo_id="local/test",
            dataset_root="dataset",
            num_frames=4,
            episode_ranges=(EpisodeFrameRange(0, 0, 4),),
            action_reader=multiple_close_actions.__getitem__,
        )


def test_index_round_trip_preserves_no_close_episode(tmp_path) -> None:
    actions = [_action(value) for value in [0.8, 0.7, 0.6, 0.6]]
    index = build_critical_frame_index(
        dataset_repo_id="local/test",
        dataset_root=tmp_path / "dataset",
        num_frames=4,
        episode_ranges=(EpisodeFrameRange(0, 0, 4),),
        action_reader=actions.__getitem__,
    )
    path = tmp_path / "critical.json"

    save_critical_frame_index(index, path)
    loaded = load_critical_frame_index(path)

    assert loaded == index
    assert loaded.episodes[0].close_frame_index is None
    assert loaded.config.weight == pytest.approx(5.0)


def test_critical_sampler_preserves_episode_boundaries_and_drop_tail() -> None:
    torch = pytest.importorskip("torch")
    del torch
    from sharedautonomy.data.critical_sampler import CriticalFrameSampler

    actions = [_action(value) for value in [0.8, 0.8, 0.8, 0.4, 0.4, 0.4, 0.4, 0.4]]
    actions.extend(_action(0.8) for _ in range(6))
    index = build_critical_frame_index(
        dataset_repo_id="local/test",
        dataset_root="dataset",
        num_frames=14,
        episode_ranges=(
            EpisodeFrameRange(0, 0, 8),
            EpisodeFrameRange(1, 8, 14),
        ),
        action_reader=actions.__getitem__,
        config=CriticalFrameConfig(pre_frames=2, post_frames=1),
    )
    sampler = CriticalFrameSampler(
        [0, 8],
        [8, 14],
        drop_n_last_frames=1,
        shuffle=False,
        critical_index=index,
    )

    assert list(sampler) == [0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12]
    assert sampler.critical_frame_count == 4


def test_critical_sampler_weighted_epoch_is_seed_reproducible() -> None:
    pytest.importorskip("torch")
    from sharedautonomy.data.critical_sampler import CriticalFrameSampler

    actions = [_action(0.8) for _ in range(20)]
    actions[10] = _action(0.4)
    index = build_critical_frame_index(
        dataset_repo_id="local/test",
        dataset_root="dataset",
        num_frames=20,
        episode_ranges=(EpisodeFrameRange(0, 0, 20),),
        action_reader=actions.__getitem__,
        config=CriticalFrameConfig(pre_frames=2, post_frames=1, weight=5.0),
    )

    first = CriticalFrameSampler(
        [0],
        [20],
        shuffle=True,
        seed=123,
        critical_index=index,
    )
    second = CriticalFrameSampler(
        [0],
        [20],
        shuffle=True,
        seed=123,
        critical_index=index,
    )

    first_epoch = list(first)
    second_epoch = list(second)
    assert first_epoch == second_epoch
    assert len(first_epoch) == 20
    assert all(0 <= frame_index < 20 for frame_index in first_epoch)
    assert first.critical_frame_count == 4

    resumed = CriticalFrameSampler(
        [0],
        [20],
        shuffle=True,
        seed=123,
        critical_index=index,
    )
    resumed.load_state_dict({"epoch": 0, "start_index": 5})
    assert list(resumed) == first_epoch[5:]
