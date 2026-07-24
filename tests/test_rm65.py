from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.core

pytest.importorskip("lerobot")

from sharedautonomy.robot.rm65 import JOINT_KEYS, RM65, RM65Config
from sharedautonomy.robot.safety import MotionDisabledError


class FakeArm:
    def __init__(self) -> None:
        self.moves = []
        self.deleted = False

    def rm_create_robot_arm(self, ip: str, port: int, level: int = 3):
        self.connection = (ip, port, level)
        return SimpleNamespace(id=7)

    def rm_set_arm_run_mode(self, mode: int) -> int:
        self.run_mode = mode
        return 0

    def rm_get_joint_degree(self):
        return 0, [0.0] * 6

    def rm_algo_forward_kinematics(self, joints, flag: int = 1):
        return [0.1, 0.2, 0.3, 0.0, 0.0, 0.0]

    def rm_movej_canfd(self, joints, **kwargs) -> int:
        self.moves.append((list(joints), kwargs))
        return 0

    def rm_delete_robot_arm(self) -> int:
        self.deleted = True
        return 0


def make_robot(tmp_path, *, enable_motion: bool) -> tuple[RM65, FakeArm]:
    arm = FakeArm()
    config = RM65Config(
        id="test-rm65",
        calibration_dir=tmp_path,
        ip="192.0.2.1",
        enable_motion=enable_motion,
        joint_limits_deg=[[-180, 180]] * 6 if enable_motion else None,
        max_relative_target_deg=1,
    )
    robot = RM65(config, arm_factory=lambda: arm, cameras_factory=lambda _: {})
    return robot, arm


def test_connect_is_read_only_and_observation_matches_features(tmp_path) -> None:
    robot, arm = make_robot(tmp_path, enable_motion=False)

    robot.connect()
    observation = robot.get_observation()

    assert arm.moves == []
    assert not hasattr(arm, "run_mode")
    assert set(observation) == set(robot.observation_features)
    with pytest.raises(MotionDisabledError):
        robot.send_action({key: 0.0 for key in JOINT_KEYS})

    robot.disconnect()
    assert arm.deleted


def test_send_action_clips_joint_step_and_returns_executed_action(tmp_path) -> None:
    robot, arm = make_robot(tmp_path, enable_motion=True)
    robot.connect()

    executed = robot.send_action({key: 10.0 for key in JOINT_KEYS})

    assert list(executed.values()) == [1.0] * 6
    assert arm.moves[0][0] == [1.0] * 6
    robot.disconnect()
