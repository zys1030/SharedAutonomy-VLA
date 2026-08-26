"""Tests for O(1) LeRobot dataset frame index resolution."""

from __future__ import annotations

import pytest
from sharedautonomy.policies.act.runtime import resolve_dataset_frame_index

pytestmark = pytest.mark.core


class _EpisodeMetaTable:
    def __init__(self, rows: list[dict[str, int]]) -> None:
        self._rows = rows

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, key: str) -> list[int]:
        return [row[key] for row in self._rows]

    @property
    def columns(self) -> list[str]:
        if not self._rows:
            return []
        return list(self._rows[0].keys())

    @property
    def index(self) -> list[int]:
        return list(range(len(self._rows)))


class _MetaEpisodesDataset:
    def __init__(self, rows: list[dict[str, int]]) -> None:
        self.meta = type("Meta", (), {"episodes": _EpisodeMetaTable(rows)})()


class _EpisodeDataIndexDataset:
    def __init__(self, starts: list[int], ends: list[int]) -> None:
        self.episode_data_index = {"from": starts, "to": ends}


def test_resolve_from_meta_episodes_by_row_order() -> None:
    dataset = _MetaEpisodesDataset(
        [
            {"dataset_from_index": 0, "dataset_to_index": 100},
            {"dataset_from_index": 100, "dataset_to_index": 250},
        ]
    )

    assert resolve_dataset_frame_index(dataset, episode_index=0, frame_index=0) == 0
    assert resolve_dataset_frame_index(dataset, episode_index=1, frame_index=5) == 105


def test_resolve_from_episode_data_index() -> None:
    dataset = _EpisodeDataIndexDataset(starts=[0, 50], ends=[50, 120])

    assert resolve_dataset_frame_index(dataset, episode_index=1, frame_index=10) == 60


def test_frame_index_out_of_range() -> None:
    dataset = _MetaEpisodesDataset([{"dataset_from_index": 0, "dataset_to_index": 3}])

    with pytest.raises(ValueError, match="frame_index=3"):
        resolve_dataset_frame_index(dataset, episode_index=0, frame_index=3)


def test_missing_episode_metadata_raises() -> None:
    with pytest.raises(ValueError, match="Cannot resolve dataset frame index"):
        resolve_dataset_frame_index(object(), episode_index=0, frame_index=0)
