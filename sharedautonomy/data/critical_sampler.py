"""LeRobot-compatible weighted sampler for grasp-critical frame starts."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Sampler

from .critical_frames import CriticalFrameIndex, load_critical_frame_index

logger = logging.getLogger(__name__)


class CriticalFrameSampler(Sampler[int]):
    """Sample LeRobot frame starts with extra probability around first grasp.

    The constructor mirrors LeRobot 0.6's ``EpisodeAwareSampler``.  Each
    yielded index is still an original dataset frame.  LeRobot's dataset
    reader therefore builds the normal contiguous action chunk from that
    start, while the episode-aware bounds prevent a chunk start from landing
    in the dropped tail of an episode.

    Weighted epochs use sampling with replacement and contain the same number
    of positions as a uniform epoch.  This keeps the optimizer/dataloader
    schedule unchanged while making critical starts more likely.
    """

    def __init__(
        self,
        dataset_from_indices: list[int],
        dataset_to_indices: list[int],
        episode_indices_to_use: list[int] | None = None,
        drop_n_first_frames: int = 0,
        drop_n_last_frames: int = 0,
        shuffle: bool = False,
        seed: int = 0,
        absolute_to_relative_idx: dict[int, int] | None = None,
        *,
        critical_index_path: str | Path | None = None,
        critical_index: CriticalFrameIndex | None = None,
    ) -> None:
        if critical_index is not None and critical_index_path is not None:
            raise ValueError("pass either critical_index or critical_index_path, not both")
        index_source = str(critical_index_path) if critical_index_path is not None else "<in-memory>"
        if critical_index is None:
            if critical_index_path is None:
                raise ValueError("critical_index_path is required")
            critical_index = load_critical_frame_index(critical_index_path)

        if int(drop_n_first_frames) < 0:
            raise ValueError("drop_n_first_frames must be >= 0")
        if int(drop_n_last_frames) < 0:
            raise ValueError("drop_n_last_frames must be >= 0")

        from_indices = np.asarray(dataset_from_indices, dtype=np.int64)
        to_indices = np.asarray(dataset_to_indices, dtype=np.int64)
        if from_indices.shape != to_indices.shape:
            raise ValueError(
                "dataset_from_indices and dataset_to_indices must have the same length, "
                f"got {len(from_indices)} and {len(to_indices)}"
            )
        self._validate_index_matches_dataset(critical_index, from_indices, to_indices)

        used = np.ones(len(from_indices), dtype=bool)
        if episode_indices_to_use is not None:
            selected = np.asarray(episode_indices_to_use, dtype=np.int64)
            if np.any(selected < 0) or np.any(selected >= len(from_indices)):
                raise ValueError("episode_indices_to_use contains an out-of-range episode index")
            used = np.zeros(len(from_indices), dtype=bool)
            used[selected] = True

        starts = from_indices + int(drop_n_first_frames)
        lengths = to_indices - int(drop_n_last_frames) - starts
        for episode_index in np.flatnonzero(used & (lengths <= 0)):
            logger.warning(
                "Episode %d has %d frames but drop_n_first_frames=%d and "
                "drop_n_last_frames=%d removes all frames. Skipping.",
                episode_index,
                to_indices[episode_index] - from_indices[episode_index],
                drop_n_first_frames,
                drop_n_last_frames,
            )
        used &= lengths > 0
        if not used.any():
            raise ValueError(
                "No valid frames remain after applying drop_n_first_frames and drop_n_last_frames."
            )

        selected_episode_indices = np.flatnonzero(used)
        self._episode_indices = selected_episode_indices
        self._starts = starts[selected_episode_indices]
        selected_lengths = lengths[selected_episode_indices]
        self._cum_lengths = np.cumsum(selected_lengths)
        self._num_frames = int(self._cum_lengths[-1])
        self.shuffle = bool(shuffle)
        self.seed = int(seed)
        self._epoch = 0
        self._start_index = 0
        self._absolute_to_relative = absolute_to_relative_idx
        self._critical_index = critical_index
        self._weights = np.ones(self._num_frames, dtype=np.float64)
        self._apply_critical_weights(
            starts=starts,
            lengths=lengths,
            selected_episode_indices=selected_episode_indices,
        )
        self._critical_frame_count = int(np.count_nonzero(self._weights > 1.0))

        logger.info(
            "CriticalFrameSampler: index=%s, %d valid starts, %d critical starts, weight=%.3f",
            index_source,
            self._num_frames,
            self._critical_frame_count,
            critical_index.config.weight,
        )

    @property
    def indices(self) -> list[int]:
        """Materialized frame indices in unshuffled order for diagnostics."""

        return [self._frame_index(position) for position in range(self._num_frames)]

    @property
    def critical_frame_count(self) -> int:
        """Number of valid frame starts receiving extra weight."""

        return self._critical_frame_count

    @property
    def critical_frame_fraction(self) -> float:
        """Fraction of valid starts receiving extra weight."""

        return self._critical_frame_count / self._num_frames

    def set_epoch(self, epoch: int) -> None:
        self._epoch = int(epoch)

    def state_dict(self) -> dict[str, int]:
        return {"epoch": self._epoch, "start_index": self._start_index}

    def load_state_dict(self, state: dict[str, int]) -> None:
        self._epoch = int(state["epoch"])
        self._start_index = int(state["start_index"])

    def _epoch_generator(self, epoch: int) -> torch.Generator:
        epoch_seed = int(np.random.SeedSequence([self.seed, epoch]).generate_state(1, dtype=np.uint64)[0])
        return torch.Generator().manual_seed(epoch_seed)

    def _frame_index(self, position: int) -> int:
        episode_position = int(np.searchsorted(self._cum_lengths, position, side="right"))
        previous_end = int(self._cum_lengths[episode_position - 1]) if episode_position > 0 else 0
        position_in_episode = position - previous_end
        absolute_index = int(self._starts[episode_position]) + position_in_episode
        if self._absolute_to_relative is not None:
            return self._absolute_to_relative[absolute_index]
        return absolute_index

    def __iter__(self) -> Iterator[int]:
        epoch, start = self._epoch, self._start_index
        self._epoch += 1
        self._start_index = 0
        return self._iter_epoch(epoch, start)

    def _iter_epoch(self, epoch: int, start: int) -> Iterator[int]:
        if self.shuffle:
            generator = self._epoch_generator(epoch)
            if self._critical_frame_count:
                weights = torch.as_tensor(self._weights, dtype=torch.double)
                order = torch.multinomial(
                    weights,
                    self._num_frames,
                    replacement=True,
                    generator=generator,
                )
            else:
                order = torch.randperm(self._num_frames, generator=generator)
            for position in range(start, self._num_frames):
                yield self._frame_index(int(order[position]))
            return

        for position in range(start, self._num_frames):
            yield self._frame_index(position)

    def __len__(self) -> int:
        return self._num_frames

    def _apply_critical_weights(
        self,
        *,
        starts: np.ndarray,
        lengths: np.ndarray,
        selected_episode_indices: np.ndarray,
    ) -> None:
        logical_offset = 0
        for episode_index in selected_episode_indices:
            episode = self._critical_index.episodes[int(episode_index)]
            valid_start = int(starts[episode_index])
            valid_end_exclusive = valid_start + int(lengths[episode_index])
            if episode.window_start_index is not None and episode.window_end_index is not None:
                critical_start = max(valid_start, episode.window_start_index)
                critical_end_exclusive = min(valid_end_exclusive, episode.window_end_index + 1)
                if critical_start < critical_end_exclusive:
                    begin = logical_offset + critical_start - valid_start
                    end = logical_offset + critical_end_exclusive - valid_start
                    self._weights[begin:end] = self._critical_index.config.weight
            logical_offset += int(lengths[episode_index])

    @staticmethod
    def _validate_index_matches_dataset(
        critical_index: CriticalFrameIndex,
        from_indices: np.ndarray,
        to_indices: np.ndarray,
    ) -> None:
        if len(critical_index.episodes) != len(from_indices):
            raise ValueError(
                "critical-frame index episode count does not match the training dataset: "
                f"{len(critical_index.episodes)} != {len(from_indices)}"
            )
        for episode_index, episode in enumerate(critical_index.episodes):
            if (
                episode.episode_index != episode_index
                or episode.from_index != int(from_indices[episode_index])
                or episode.to_index != int(to_indices[episode_index])
            ):
                raise ValueError(
                    "critical-frame index does not match dataset episode boundaries at "
                    f"episode {episode_index}"
                )
