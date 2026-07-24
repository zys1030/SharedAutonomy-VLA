"""Read-only Cartesian dry-run using real UDP state. Never enables motion."""

from __future__ import annotations

import argparse
import json
import sys
import time

from sharedautonomy.assistance.safety_filter import CartesianSafetyFilter, CartesianSafetyLimits
from sharedautonomy.control.manual import (
    ManualCartesianConfig,
    MockJointCommander,
    build_manual_cartesian_runner,
)
from sharedautonomy.control.realtime import RealtimeCartesianStateSource
from sharedautonomy.devices.spacemouse import MockSpaceMouse, SpaceMouseConfig
from sharedautonomy.robot.kinematics import RealManInverseKinematics
from sharedautonomy.robot.realtime_state import RealManRealtimeStateSource
from sharedautonomy.robot.safety import CartesianWorkspace


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Connect to RM-65B UDP realtime push and run a read-only Cartesian dry-run. "
            "Uses mock SpaceMouse (zero command by default). Never sends motion commands."
        )
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--control-hz", type=float, default=50.0)
    parser.add_argument(
        "--raw-tz",
        type=float,
        default=0.0,
        help="Optional mock SpaceMouse +Z stick in [-1, 1]; default 0 holds pose",
    )
    return parser.parse_args()


def _permissive_bringup_workspace() -> CartesianWorkspace:
    """Wide bounds for first UDP bring-up. Stamp geometry remains the formal workspace."""
    return CartesianWorkspace(
        polygon_xy_m=[[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]],
        table_z_m=-1.0,
        min_tool_clearance_m=0.0,
        tool_tip_offset_base_m=[0.0, 0.0, -0.178],
        max_flange_z_m=None,
    )


def main() -> int:
    args = parse_args()
    if args.steps < 1:
        raise ValueError("steps must be >= 1")

    backend = RealManRealtimeStateSource(ip=args.ip, port=args.port)
    backend.connect()
    try:
        first = backend.read_snapshot()
        identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        teleop = MockSpaceMouse(
            SpaceMouseConfig(
                deadzone=0.0,
                max_linear_speed_m_s=0.05,
                mount_orientation="custom",
                translation_transform=identity,
                rotation_transform=identity,
            ),
            translation_raw=(0.0, 0.0, args.raw_tz),
            deadman_active=True,
            input_age_ms=0.0,
        )
        fixed_rpy = tuple(float(value) for value in first.ee_rpy_rad)
        safety = CartesianSafetyFilter(
            workspace=_permissive_bringup_workspace(),
            limits=CartesianSafetyLimits(
                max_speed_m_s=0.05,
                max_acceleration_m_s2=0.25,
                fixed_ee_rpy_rad=fixed_rpy,
                robot_state_timeout_s=0.05,
            ),
        )
        # Use offline Algo IK so the live UDP handle is only a state source.
        runner = build_manual_cartesian_runner(
            config=ManualCartesianConfig(
                control_rate_hz=args.control_hz,
                enable_motion=False,
                fixed_ee_rpy_rad=fixed_rpy,
            ),
            teleop=teleop,
            robot_state_source=RealtimeCartesianStateSource(backend),
            safety_filter=safety,
            inverse_kinematics=RealManInverseKinematics.offline_rm65(),
            joint_commander=MockJointCommander(),
        )

        period_s = 1.0 / float(args.control_hz)
        steps = []
        for index in range(args.steps):
            now_ns = time.perf_counter_ns()
            steps.append(runner.step(now_monotonic_ns=now_ns, dt_s=period_s))
            if index + 1 < args.steps:
                time.sleep(period_s)

        summary = {
            "status": "ok",
            "enable_motion": False,
            "workspace_mode": "permissive_bringup",
            "steps": len(steps),
            "udp_realtime_push": {
                "enable": True,
                "cycle": None if backend.realtime_config is None else backend.realtime_config.get("cycle"),
            },
            "callback_error_count": backend.cache.callback_error_count,
            "first_robot_state_age_ms": steps[0].robot_state.robot_state_age_ms,
            "last_robot_state_age_ms": steps[-1].robot_state.robot_state_age_ms,
            "first_ee_position_m": list(steps[0].robot_state.ee_position_m),
            "last_ee_position_m": list(steps[-1].robot_state.ee_position_m),
            "first_joint_target_deg": list(steps[0].executed_action.joint_target_deg or ()),
            "motion_sent_any": any(step.motion_sent for step in steps),
            "safety_intervened_any": any(step.executed_action.safety_intervened for step in steps),
            "safety_reasons": sorted(
                {reason for step in steps for reason in step.executed_action.safety_reasons}
            ),
        }
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        backend.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
