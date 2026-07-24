import pytest

pytestmark = pytest.mark.extended

from sharedautonomy.robot.gripper import (
    GripperDirection,
    RealManControllerGripper,
    SerialSoftGripper,
)


class FakeArm:
    def __init__(self) -> None:
        self.command = None

    def rm_set_gripper_position(self, position: int, block: bool, timeout: int) -> int:
        self.command = (position, block, timeout)
        return 0

    def rm_get_gripper_state(self):
        return 0, {"actpos": 321}


def test_realman_controller_gripper_clips_and_reports_position() -> None:
    arm = FakeArm()
    gripper = RealManControllerGripper(arm, position_min=1, position_max=1000)

    assert gripper.set_position(1200) == 1000
    assert arm.command == (1000, False, 0)
    assert gripper.get_position() == 321


def test_legacy_serial_gripper_command_has_valid_frame_and_checksum() -> None:
    command = SerialSoftGripper.build_command(
        GripperDirection.CLOSE,
        angle_deg=1872,
        speed_rad_s=20,
    )

    assert command[0] == SerialSoftGripper.FRAME_HEAD
    assert command[-1] == SerialSoftGripper.FRAME_TAIL
    assert command[5:7] == bytes([0x49, 0x20])
    assert command[7:9] == bytes([0x00, 0xC8])

    checksum = 0
    for value in command[:-2]:
        checksum ^= value
    assert command[-2] == checksum


def test_legacy_serial_gripper_enforces_protocol_angle_limit() -> None:
    SerialSoftGripper.build_command(
        GripperDirection.CLOSE,
        angle_deg=6553.5,
        speed_rad_s=20,
    )

    with pytest.raises(ValueError, match="angle_deg is too large"):
        SerialSoftGripper.build_command(
            GripperDirection.CLOSE,
            angle_deg=6553.6,
            speed_rad_s=20,
        )
