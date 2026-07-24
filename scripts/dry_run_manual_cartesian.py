"""Offline dry-run entry for the manual Cartesian SpaceMouse runner."""

from __future__ import annotations

import argparse
import json

from sharedautonomy.assistance.safety_filter import (
    CartesianSafetyFilter,
    CartesianSafetyLimits,
    stamp_cartesian_workspace,
)
from sharedautonomy.control.manual import (
    ManualCartesianConfig,
    MockInverseKinematics,
    MockJointCommander,
    MockRobotStateSource,
    build_manual_cartesian_runner,
)
from sharedautonomy.devices.spacemouse import MockSpaceMouse, SpaceMouseConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run an offline mock dry-run of the manual Cartesian SpaceMouse runner "
            "with the Cartesian safety chain enabled. Never enables real robot motion."
        )
    )
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--control-hz", type=float, default=50.0)
    parser.add_argument("--raw-tx", type=float, default=0.0)
    parser.add_argument("--raw-ty", type=float, default=0.0)
    parser.add_argument("--raw-tz", type=float, default=1.0)
    parser.add_argument(
        "--outside-workspace",
        action="store_true",
        help="Start near the XY boundary and request an out-of-bounds step",
    )
    parser.add_argument(
        "--mock-ik",
        action="store_true",
        help="Use MockInverseKinematics instead of the offline RealMan Algo IK",
    )
    return parser.parse_args()


def _build_inverse_kinematics(*, mock_ik: bool):
    if mock_ik:
        return MockInverseKinematics(), "mock"
    try:
        from sharedautonomy.robot.kinematics import RealManInverseKinematics

        return RealManInverseKinematics.offline_rm65(), "realman_offline_algo"
    except ImportError:
        return MockInverseKinematics(), "mock_fallback_no_robotic_arm"


def _workspace_consistent_state(
    seed_joints: list[float],
) -> tuple[list[float], list[float], list[float]]:
    """Return joints/pose inside the stamp workspace for offline RealMan dry-runs."""
    from sharedautonomy.robot.kinematics import create_rm65_offline_algo, solve_inverse_kinematics

    algo = create_rm65_offline_algo()
    pose = [float(value) for value in algo.rm_algo_forward_kinematics(seed_joints, flag=1)]
    rpy_rad = pose[3:6]
    workspace = stamp_cartesian_workspace()
    max_z = 0.40 if workspace.max_flange_z_m is None else min(0.40, float(workspace.max_flange_z_m) - 0.01)
    # Known-safe interior XY from Day-1 tests; clamp Z under the provisional ceiling.
    safe_position = [-0.30, -0.10, min(float(pose[2]), max_z)]
    if safe_position[2] < workspace.min_flange_z_m:
        safe_position[2] = float(workspace.min_flange_z_m) + 0.02
    joints = solve_inverse_kinematics(
        algo,
        joint_seed_deg=seed_joints,
        target_position_m=safe_position,
        target_rpy_rad=rpy_rad,
    )
    resolved = [float(value) for value in algo.rm_algo_forward_kinematics(joints, flag=1)]
    return joints, resolved[:3], resolved[3:6]


def main() -> None:
    args = parse_args()
    if args.steps < 1:
        raise ValueError("steps must be >= 1")

    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    seed_joints = [0.0, 15.0, 15.0, 0.0, 120.0, 0.0]
    # Outside-workspace demo fabricates an EE pose near the polygon edge that may not
    # match the joint seed; keep mock IK there so the summary highlights workspace hold.
    inverse_kinematics, ik_backend = _build_inverse_kinematics(
        mock_ik=args.mock_ik or args.outside_workspace
    )

    if args.outside_workspace:
        raw_translation = (1.0, 0.0, 0.0)
        teleop_speed = 5.0
        safety_speed = 5.0
        robot = MockRobotStateSource(ee_position_m=(-0.155, -0.05, 0.25), joint_position_deg=seed_joints)
    elif ik_backend.startswith("realman"):
        joints, position_m, rpy_rad = _workspace_consistent_state(seed_joints)
        raw_translation = (args.raw_tx, args.raw_ty, args.raw_tz)
        teleop_speed = 0.05
        safety_speed = 0.05
        robot = MockRobotStateSource(
            ee_position_m=position_m,
            ee_rpy_rad=rpy_rad,
            joint_position_deg=joints,
        )
    else:
        raw_translation = (args.raw_tx, args.raw_ty, args.raw_tz)
        teleop_speed = 0.05
        safety_speed = 0.05
        robot = MockRobotStateSource(joint_position_deg=seed_joints)

    teleop = MockSpaceMouse(
        SpaceMouseConfig(
            deadzone=0.0,
            max_linear_speed_m_s=teleop_speed,
            mount_orientation="custom",
            translation_transform=identity,
            rotation_transform=identity,
            input_timeout_s=0.1,
        ),
        translation_raw=raw_translation,
        deadman_active=True,
        input_age_ms=0.0,
    )
    fixed_rpy = tuple(float(value) for value in robot.ee_rpy_rad)
    safety = CartesianSafetyFilter(
        workspace=stamp_cartesian_workspace(),
        limits=CartesianSafetyLimits(
            max_speed_m_s=safety_speed,
            max_acceleration_m_s2=max(safety_speed / 0.02, 0.25),
            fixed_ee_rpy_rad=fixed_rpy,
        ),
    )
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(
            control_rate_hz=args.control_hz,
            enable_motion=False,
            fixed_ee_rpy_rad=fixed_rpy,
        ),
        teleop=teleop,
        robot_state_source=robot,
        safety_filter=safety,
        inverse_kinematics=inverse_kinematics,
        joint_commander=MockJointCommander(),
    )
    steps = runner.run_dry_run(steps=args.steps)
    first = steps[0]
    safe_position = [
        present + velocity * first.actual_dt_s
        for present, velocity in zip(
            first.robot_state.ee_position_m,
            first.executed_action.linear_velocity_m_s,
            strict=True,
        )
    ]
    summary = {
        "steps": len(steps),
        "enable_motion": False,
        "ik_backend": ik_backend,
        "motion_sent_any": any(step.motion_sent for step in steps),
        "first_requested_ee_position_m": list(first.requested_ee_position_m),
        "first_safe_ee_position_m": safe_position,
        "first_joint_target_deg": list(first.executed_action.joint_target_deg or ()),
        "safety_intervened_any": any(step.executed_action.safety_intervened for step in steps),
        "safety_reasons": sorted(
            {reason for step in steps for reason in step.executed_action.safety_reasons}
        ),
        "actual_dt_s": first.actual_dt_s,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
