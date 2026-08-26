"""Unit tests for RealMan inverse-kinematics helpers."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.core

from sharedautonomy.assistance.safety_filter import (
    CartesianSafetyFilter,
    CartesianSafetyLimits,
    example_cartesian_workspace,
)
from sharedautonomy.control.manual import (
    ManualCartesianConfig,
    MockJointCommander,
    MockRobotStateSource,
    build_manual_cartesian_runner,
)
from sharedautonomy.devices.spacemouse import MockSpaceMouse, SpaceMouseConfig
from sharedautonomy.robot.kinematics import (
    InverseKinematicsError,
    RealManInverseKinematics,
    create_rm65_offline_algo,
    solve_inverse_kinematics,
)


class FakeIkSolver:
    def __init__(self, *, ret_code: int = 0, joints: list[float] | None = None) -> None:
        self.ret_code = ret_code
        self.joints = joints or [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        self.calls: list[object] = []

    def rm_algo_inverse_kinematics(self, params: object) -> tuple[int, list[float]]:
        self.calls.append(params)
        return self.ret_code, list(self.joints)


def test_solve_inverse_kinematics_uses_flag_euler_and_returns_six_joints() -> None:
    pytest.importorskip("Robotic_Arm")
    solver = FakeIkSolver(joints=[10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 99.0])
    joints = solve_inverse_kinematics(
        solver,
        joint_seed_deg=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
        target_position_m=[-0.3, -0.1, 0.25],
        target_rpy_rad=[0.0, 1.57, 0.0],
    )

    assert joints == [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    assert len(solver.calls) == 1
    params = solver.calls[0]
    assert int(params.flag) == 1


def test_solve_inverse_kinematics_raises_on_nonzero_status() -> None:
    pytest.importorskip("Robotic_Arm")
    solver = FakeIkSolver(ret_code=1)
    with pytest.raises(InverseKinematicsError, match="status 1"):
        solve_inverse_kinematics(
            solver,
            joint_seed_deg=[0.0] * 6,
            target_position_m=[0.1, 0.2, 0.3],
            target_rpy_rad=[0.0, 0.0, 0.0],
        )


def test_runner_holds_on_ik_failure() -> None:
    class FailingIk:
        def solve(self, **kwargs):
            del kwargs
            raise InverseKinematicsError("boom")

    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    teleop = MockSpaceMouse(
        SpaceMouseConfig(
            deadzone=0.0,
            max_linear_speed_m_s=0.05,
            mount_orientation="custom",
            translation_transform=identity,
            rotation_transform=identity,
        ),
        translation_raw=(0.0, 0.0, 1.0),
        deadman_active=True,
    )
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(enable_motion=False),
        teleop=teleop,
        robot_state_source=MockRobotStateSource(),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(),
        ),
        inverse_kinematics=FailingIk(),
        joint_commander=MockJointCommander(),
    )

    step = runner.step(now_monotonic_ns=1_000_000_000, dt_s=0.02)

    assert step.executed_action.safety_intervened is True
    assert "ik_failure" in step.executed_action.safety_reasons
    assert step.executed_action.joint_target_deg == pytest.approx((0.0, 15.0, 15.0, 0.0, 120.0, 0.0))
    assert step.executed_action.linear_velocity_m_s == pytest.approx((0.0, 0.0, 0.0))


def test_offline_rm65_ik_roundtrip_near_seed() -> None:
    pytest.importorskip("Robotic_Arm")
    algo = create_rm65_offline_algo()
    ik = RealManInverseKinematics.from_arm(algo)
    seed = [0.0, 15.0, 15.0, 0.0, 120.0, 0.0]
    pose = algo.rm_algo_forward_kinematics(seed, flag=1)
    joints = ik.solve(
        joint_seed_deg=seed,
        target_position_m=pose[:3],
        target_rpy_rad=pose[3:6],
    )
    assert joints == pytest.approx(seed, abs=1e-2)
