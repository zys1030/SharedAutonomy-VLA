"""Core tests for stamp-style serial soft-gripper teleop."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.core

from sharedautonomy.data.schema import CoordinateFrame, HumanAction, SampleTimestamp
from sharedautonomy.devices.spacemouse import (
    SpaceMouseAxes,
    SpaceMouseConfig,
    spacemouse_axes_to_human_action,
)
from sharedautonomy.robot.gripper import (
    GripperDirection,
    SerialSoftGripperConfig,
    SerialSoftGripperTeleop,
    SerialSoftGripperTeleopConfig,
)


class _RecordingGripper:
    def __init__(self) -> None:
        self.calls: list[tuple[GripperDirection, float, float]] = []
        # Mirror SerialSoftGripper.config used by _settle_after_pulse().
        self.config = SerialSoftGripperConfig(port="TEST", response_delay_s=0.0)

    def send_motion(
        self,
        direction: GripperDirection,
        *,
        angle_deg: float,
        speed_rad_s: float,
    ) -> bytes:
        self.calls.append((direction, angle_deg, speed_rad_s))
        return b""


def _human_action(
    *,
    open_fraction: float | None,
    edge: bool,
) -> HumanAction:
    return HumanAction(
        timestamp=SampleTimestamp(
            timestamp_utc=datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
            received_monotonic_ns=1,
        ),
        linear_velocity_m_s=(0.0, 0.0, 0.0),
        angular_velocity_rad_s=(0.0, 0.0, 0.0),
        gripper_target_open_fraction=open_fraction,
        deadman_active=True,
        input_age_ms=0.0,
        reference_frame=CoordinateFrame.BASE,
        gripper_button_edge=edge,
    )


def test_serial_gripper_teleop_sends_stamp_open_close_on_button_edge() -> None:
    gripper = _RecordingGripper()
    teleop = SerialSoftGripperTeleop(
        gripper,  # type: ignore[arg-type]
        SerialSoftGripperTeleopConfig(),
    )

    assert teleop.apply_human_gripper(_human_action(open_fraction=1.0, edge=False)) == 1.0
    assert gripper.calls == []

    assert teleop.apply_human_gripper(_human_action(open_fraction=0.0, edge=True)) == 0.0
    assert gripper.calls == [(GripperDirection.CLOSE, 1872.0, 20.0)]

    assert teleop.apply_human_gripper(_human_action(open_fraction=1.0, edge=True)) == 1.0
    assert gripper.calls[-1] == (GripperDirection.OPEN, 1800.0, 20.0)
    assert teleop.commands_sent == 2


def test_serial_gripper_teleop_open_uses_working_open_fraction() -> None:
    gripper = _RecordingGripper()
    teleop = SerialSoftGripperTeleop(
        gripper,  # type: ignore[arg-type]
        SerialSoftGripperTeleopConfig(working_open_fraction=0.65),
    )

    assert teleop.apply_human_gripper(_human_action(open_fraction=1.0, edge=True)) == 1.0
    assert gripper.calls == [(GripperDirection.OPEN, 1170.0, 20.0)]

    assert teleop.apply_human_gripper(_human_action(open_fraction=0.0, edge=True)) == 0.0
    assert gripper.calls[-1] == (GripperDirection.CLOSE, 1872.0, 20.0)

    assert teleop.apply_human_gripper(_human_action(open_fraction=1.0, edge=True)) == 1.0
    assert gripper.calls[-1] == (GripperDirection.OPEN, 1170.0, 20.0)


def test_serial_gripper_move_to_working_open_closes_then_partial_opens() -> None:
    gripper = _RecordingGripper()
    teleop = SerialSoftGripperTeleop(
        gripper,  # type: ignore[arg-type]
        SerialSoftGripperTeleopConfig(working_open_fraction=0.6),
    )

    close_angle, open_angle = teleop.move_to_working_open(0.6)
    assert close_angle == 1872.0
    assert open_angle == 1080.0
    assert gripper.calls == [
        (GripperDirection.CLOSE, 1872.0, 20.0),
        (GripperDirection.OPEN, 1080.0, 20.0),
    ]
    assert teleop.commands_sent == 2
    assert teleop.commanded_open_fraction == 1.0


def test_serial_gripper_open_to_fraction_scales_ready_open_pulse() -> None:
    gripper = _RecordingGripper()
    teleop = SerialSoftGripperTeleop(
        gripper,  # type: ignore[arg-type]
        SerialSoftGripperTeleopConfig(open_angle_deg=1800.0),
    )

    assert teleop.open_to_fraction(0.65) == 1.0
    assert gripper.calls == [(GripperDirection.OPEN, 1170.0, 20.0)]

    assert teleop.open_to_fraction(1.0) == 1.0
    assert gripper.calls[-1] == (GripperDirection.OPEN, 1800.0, 20.0)

    assert teleop.open_to_fraction(0.0) == 0.0
    assert gripper.calls[-1] == (GripperDirection.CLOSE, 1872.0, 20.0)


def test_spacemouse_human_action_carries_gripper_button_edge() -> None:
    axes = SpaceMouseAxes(
        translation=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, 0.0),
        deadman_active=True,
        gripper_button_edge=True,
        received_monotonic_ns=5,
        input_age_ms=0.0,
    )
    action = spacemouse_axes_to_human_action(
        axes,
        config=SpaceMouseConfig(),
        timestamp_utc=datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
        gripper_target_open_fraction=0.0,
    )
    assert action.gripper_button_edge is True
    assert action.gripper_target_open_fraction == 0.0
