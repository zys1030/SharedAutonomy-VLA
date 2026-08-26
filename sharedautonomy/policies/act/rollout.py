"""ACT rollout control loop: chunk playback with blocking or async infer refill."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import numpy as np

from sharedautonomy.data.schema import HumanAction  # noqa: F401 — break robot import cycle
from sharedautonomy.policies.act.protocol import ACTION_DIM, InferObservation, InferResponse
from sharedautonomy.robot.gripper import (
    DEFAULT_GRIPPER_CLOSE_THRESHOLD,
    DEFAULT_GRIPPER_HYSTERESIS,
    logical_gripper_is_open,
    validate_gripper_hysteresis_band,
)
from sharedautonomy.robot.safety import clip_joint_targets

logger = logging.getLogger(__name__)

InferFn = Callable[[InferObservation], tuple[InferResponse, dict[str, Any], float]]


class InferMode(str, Enum):
    BLOCKING = "blocking"
    ASYNC = "async"


@dataclass(frozen=True)
class ActRolloutConfig:
    """Chunk playback and safety limits for ACT rollout."""

    control_hz: float = 10.0
    reset_every: int = 25
    n_action_steps: int | None = None
    infer_mode: InferMode = InferMode.BLOCKING
    max_joint_step_deg: float = 5.0
    joint_limits_deg: Sequence[Sequence[float]] | None = None
    gripper_close_threshold: float = DEFAULT_GRIPPER_CLOSE_THRESHOLD
    gripper_hysteresis: float = DEFAULT_GRIPPER_HYSTERESIS

    def __post_init__(self) -> None:
        if float(self.control_hz) <= 0.0:
            raise ValueError("control_hz must be positive")
        if int(self.reset_every) < 0:
            raise ValueError("reset_every must be >= 0")
        if self.n_action_steps is not None and int(self.n_action_steps) < 1:
            raise ValueError("n_action_steps must be >= 1 when set")
        if float(self.max_joint_step_deg) <= 0.0:
            raise ValueError("max_joint_step_deg must be positive")
        validate_gripper_hysteresis_band(
            close_threshold=self.gripper_close_threshold,
            hysteresis=self.gripper_hysteresis,
        )

    @property
    def period_s(self) -> float:
        return 1.0 / float(self.control_hz)

    @property
    def blind_window_steps(self) -> int:
        """How many actions to dequeue locally before the next replan."""
        if self.reset_every == 0:
            cap = self.n_action_steps
            return 1 if cap is None else int(cap)
        interval = int(self.reset_every)
        if self.n_action_steps is None:
            return interval
        return min(interval, int(self.n_action_steps))


@dataclass
class ActRolloutStepResult:
    step_index: int
    replan: bool
    queue_depth_before: int
    queue_depth_after: int
    rtt_ms: float | None
    encode_ms: float | None
    server_timings_ms: dict[str, Any] | None
    state: list[float]
    raw_action: list[float]
    safe_joints_deg: list[float]
    gripper_open_fraction: float
    gripper_commanded: bool
    motion_sent: bool
    sync_warnings: list[str]
    wrist_age_ms: float | None
    external_age_ms: float | None


class GripperActuator(Protocol):
    def command_open_fraction(self, open_fraction: float) -> bool: ...


@dataclass
class NoOpGripperActuator:
    def command_open_fraction(self, open_fraction: float) -> bool:
        return False


@dataclass
class ThresholdGripperActuator:
    """Pulse the serial soft gripper only when open/close classification changes.

    Physical reopen uses ``teleop.working_open_fraction`` (demo-matched half-open),
    not full travel. Logical commanded state remains 0/1 via the teleop.
    """

    teleop: Any
    close_threshold: float = DEFAULT_GRIPPER_CLOSE_THRESHOLD
    hysteresis: float = DEFAULT_GRIPPER_HYSTERESIS
    _commanded_open: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        validate_gripper_hysteresis_band(
            close_threshold=self.close_threshold,
            hysteresis=self.hysteresis,
        )

    def command_open_fraction(self, open_fraction: float) -> bool:
        target_open = logical_gripper_is_open(
            open_fraction,
            commanded_open=self._commanded_open,
            close_threshold=self.close_threshold,
            hysteresis=self.hysteresis,
        )
        if target_open == self._commanded_open:
            return False
        physical_open = float(self.teleop.working_open_fraction) if target_open else 0.0
        self.teleop.open_to_fraction(physical_open)
        self._commanded_open = target_open
        return True


@dataclass
class ActChunkPlayer:
    """Local ACT action queue filled after each replan."""

    config: ActRolloutConfig
    _queue: deque[np.ndarray] = field(default_factory=deque, init=False)
    _steps_since_replan: int = field(default=0, init=False)
    _refill_future: Future[list[np.ndarray]] | None = field(default=None, init=False)
    _executor: ThreadPoolExecutor | None = field(default=None, init=False)
    _refill_obs: InferObservation | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.config.infer_mode == InferMode.ASYNC:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="act-infer")

    def close(self) -> None:
        if self._refill_future is not None:
            self._refill_future.cancel()
            self._refill_future = None
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    @property
    def queue_depth(self) -> int:
        return len(self._queue)

    def needs_replan(self, *, step_index: int) -> bool:
        if step_index == 0:
            return True
        if self._steps_since_replan >= self.config.blind_window_steps:
            return True
        return self.queue_depth == 0 and not self._refill_in_progress()

    def _refill_in_progress(self) -> bool:
        return self._refill_future is not None and not self._refill_future.done()

    def wait_for_async_refill(self) -> None:
        if self._refill_future is None:
            return
        try:
            actions = self._refill_future.result()
        except Exception:
            logger.exception("async ACT chunk refill failed")
            raise
        finally:
            self._refill_future = None
        for action in actions:
            self._queue.append(action)

    def pop_action(self) -> np.ndarray:
        if not self._queue:
            raise RuntimeError("ACT action queue is empty")
        self._steps_since_replan += 1
        return self._queue.popleft()

    def replan(
        self,
        *,
        obs: InferObservation,
        infer_fn: InferFn,
    ) -> tuple[np.ndarray, float | None, float | None, dict[str, Any] | None]:
        if self._refill_future is not None:
            self.wait_for_async_refill()

        self._queue.clear()
        self._steps_since_replan = 0
        self._refill_obs = obs

        response, raw, rtt_ms = infer_fn(obs)
        first_action = np.asarray(response.action, dtype=np.float32).reshape(ACTION_DIM)

        refill_count = max(0, self.config.blind_window_steps - 1)
        if refill_count == 0:
            self._steps_since_replan = 1
            return first_action, rtt_ms, raw.get("encode_ms"), raw.get("server_timings_ms")

        if self.config.infer_mode == InferMode.BLOCKING:
            self._blocking_refill(obs=obs, infer_fn=infer_fn, refill_count=refill_count)
        else:
            assert self._executor is not None
            self._refill_future = self._executor.submit(
                self._refill_worker,
                obs,
                infer_fn,
                refill_count,
            )

        self._steps_since_replan = 1
        return first_action, rtt_ms, raw.get("encode_ms"), raw.get("server_timings_ms")

    def _blocking_refill(
        self,
        *,
        obs: InferObservation,
        infer_fn: InferFn,
        refill_count: int,
    ) -> None:
        dequeue_obs = InferObservation(
            state=obs.state.copy(),
            wrist_rgb_hwc=obs.wrist_rgb_hwc,
            external_rgb_hwc=obs.external_rgb_hwc,
            task=obs.task,
            reset=False,
        )
        for _ in range(refill_count):
            response, _, _ = infer_fn(dequeue_obs)
            self._queue.append(np.asarray(response.action, dtype=np.float32).reshape(ACTION_DIM).copy())

    @staticmethod
    def _refill_worker(
        obs: InferObservation,
        infer_fn: InferFn,
        refill_count: int,
    ) -> list[np.ndarray]:
        dequeue_obs = InferObservation(
            state=obs.state.copy(),
            wrist_rgb_hwc=obs.wrist_rgb_hwc,
            external_rgb_hwc=obs.external_rgb_hwc,
            task=obs.task,
            reset=False,
        )
        actions: list[np.ndarray] = []
        for _ in range(refill_count):
            response, _, _ = infer_fn(dequeue_obs)
            actions.append(np.asarray(response.action, dtype=np.float32).reshape(ACTION_DIM).copy())
        return actions

    def maybe_top_up_before_pop(self, *, infer_fn: InferFn) -> None:
        if self.config.infer_mode != InferMode.ASYNC:
            return
        if self._refill_future is not None and self._refill_future.done():
            self.wait_for_async_refill()


def clip_act_action(
    *,
    present_joints_deg: Sequence[float],
    action: Sequence[float],
    max_joint_step_deg: float | Sequence[float],
    joint_limits_deg: Sequence[Sequence[float]] | None,
) -> tuple[list[float], float]:
    action_vec = np.asarray(action, dtype=np.float32).reshape(ACTION_DIM)
    joints = [float(value) for value in action_vec[:6]]
    gripper = float(np.clip(action_vec[6], 0.0, 1.0))
    safe_joints = clip_joint_targets(
        present_joints_deg,
        joints,
        max_joint_step_deg,
        joint_limits_deg,
    )
    return safe_joints, gripper


@dataclass
class ActRolloutLoop:
    """10 Hz observe → chunk playback → optional joint+gripper dispatch."""

    config: ActRolloutConfig
    infer_fn: InferFn
    player: ActChunkPlayer
    gripper: GripperActuator = field(default_factory=NoOpGripperActuator)
    motion_enabled: bool = False
    joint_commander: Any | None = None

    def run_step(
        self,
        *,
        step_index: int,
        obs: InferObservation,
        present_joints_deg: Sequence[float],
        sync_warnings: Sequence[str] | None = None,
        wrist_age_ms: float | None = None,
        external_age_ms: float | None = None,
    ) -> ActRolloutStepResult:
        self.player.maybe_top_up_before_pop(infer_fn=self.infer_fn)

        queue_before = self.player.queue_depth
        replan = self.player.needs_replan(step_index=step_index)
        rtt_ms: float | None = None
        encode_ms: float | None = None
        server_timings_ms: dict[str, Any] | None = None

        if replan:
            replan_obs = InferObservation(
                state=obs.state.copy(),
                wrist_rgb_hwc=obs.wrist_rgb_hwc,
                external_rgb_hwc=obs.external_rgb_hwc,
                task=obs.task,
                reset=True,
            )
            action, rtt_ms, encode_ms, server_timings_ms = self.player.replan(
                obs=replan_obs,
                infer_fn=self.infer_fn,
            )
        else:
            action = self.player.pop_action()

        safe_joints, gripper_fraction = clip_act_action(
            present_joints_deg=present_joints_deg,
            action=action,
            max_joint_step_deg=self.config.max_joint_step_deg,
            joint_limits_deg=self.config.joint_limits_deg,
        )
        gripper_commanded = self.gripper.command_open_fraction(gripper_fraction)
        motion_sent = False
        if self.motion_enabled:
            if self.joint_commander is None:
                raise RuntimeError("joint_commander is required when motion_enabled=True")
            self.joint_commander.send_joint_target(safe_joints)
            motion_sent = True

        return ActRolloutStepResult(
            step_index=step_index,
            replan=replan,
            queue_depth_before=queue_before,
            queue_depth_after=self.player.queue_depth,
            rtt_ms=rtt_ms,
            encode_ms=encode_ms,
            server_timings_ms=server_timings_ms,
            state=[round(float(x), 4) for x in obs.state.tolist()],
            raw_action=[round(float(x), 4) for x in np.asarray(action).reshape(ACTION_DIM).tolist()],
            safe_joints_deg=[round(float(x), 4) for x in safe_joints],
            gripper_open_fraction=round(gripper_fraction, 4),
            gripper_commanded=gripper_commanded,
            motion_sent=motion_sent,
            sync_warnings=list(sync_warnings or []),
            wrist_age_ms=wrist_age_ms,
            external_age_ms=external_age_ms,
        )

    def close(self) -> None:
        self.player.close()


def rate_limit_sleep(*, loop_start_s: float, period_s: float) -> float:
    elapsed = time.perf_counter() - loop_start_s
    sleep_s = period_s - elapsed
    if sleep_s > 0.0:
        time.sleep(sleep_s)
    return elapsed
