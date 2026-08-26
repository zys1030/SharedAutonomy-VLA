"""RealMan forward/inverse kinematics helpers with lazy SDK imports."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class InverseKinematicsError(RuntimeError):
    """Raised when RealMan inverse kinematics fails to find a joint solution."""


def solve_inverse_kinematics(
    solver: Any,
    *,
    joint_seed_deg: Sequence[float],
    target_position_m: Sequence[float],
    target_rpy_rad: Sequence[float],
) -> list[float]:
    """Solve IK with RealMan ``rm_algo_inverse_kinematics`` (euler, flag=1).

    Units follow the SDK contract:
    - ``joint_seed_deg`` / returned joints: degrees
    - ``target_position_m``: metres
    - ``target_rpy_rad``: radians
    """
    try:
        from Robotic_Arm.rm_robot_interface import rm_inverse_kinematics_params_t
    except ImportError as exc:  # pragma: no cover - depends on hardware env
        raise ImportError(
            "RealMan inverse kinematics requires the Robotic_Arm package. "
            "Install the project's hardware extra in sharedautonomy-lr060-cf."
        ) from exc

    seed = [float(value) for value in joint_seed_deg]
    if len(seed) < 6:
        raise ValueError(f"joint_seed_deg must contain at least 6 values, got {len(seed)}")
    position = [float(value) for value in target_position_m]
    rpy = [float(value) for value in target_rpy_rad]
    if len(position) != 3 or len(rpy) != 3:
        raise ValueError("target_position_m and target_rpy_rad must each contain 3 values")

    q_in = list(seed[:6])
    while len(q_in) < 7:
        q_in.append(0.0)
    q_pose = [*position, *rpy]
    params = rm_inverse_kinematics_params_t(q_in=q_in, q_pose=q_pose, flag=1)
    ret_code, joints = solver.rm_algo_inverse_kinematics(params)
    if int(ret_code) != 0:
        raise InverseKinematicsError(
            f"RealMan inverse kinematics failed with status {ret_code} "
            f"for target pose {q_pose} from seed {seed[:6]}"
        )
    if len(joints) < 6:
        raise InverseKinematicsError(
            f"RealMan inverse kinematics returned {len(joints)} joints, expected at least 6"
        )
    return [float(value) for value in joints[:6]]


def create_rm65_offline_algo() -> Any:
    """Create a standalone RM-65B Algo context that does not need a robot IP."""
    try:
        from Robotic_Arm.rm_robot_interface import Algo, rm_force_type_e, rm_robot_arm_model_e
    except ImportError as exc:  # pragma: no cover - depends on hardware env
        raise ImportError(
            "Offline RealMan kinematics requires the Robotic_Arm package. "
            "Install the project's hardware extra in sharedautonomy-lr060-cf."
        ) from exc
    return Algo(rm_robot_arm_model_e.RM_MODEL_RM_65_E, rm_force_type_e.RM_MODEL_RM_B_E)


class RealManInverseKinematics:
    """``InverseKinematics`` protocol adapter over a RealMan Algo/RoboticArm object."""

    def __init__(self, solver: Any) -> None:
        self._solver = solver

    @classmethod
    def offline_rm65(cls) -> RealManInverseKinematics:
        """Build an offline RM-65B IK solver (no TCP connection required)."""
        return cls(create_rm65_offline_algo())

    @classmethod
    def from_arm(cls, arm: Any) -> RealManInverseKinematics:
        """Wrap a connected ``RoboticArm`` / Algo instance."""
        return cls(arm)

    def solve(
        self,
        *,
        joint_seed_deg: Sequence[float],
        target_position_m: Sequence[float],
        target_rpy_rad: Sequence[float],
    ) -> list[float]:
        return solve_inverse_kinematics(
            self._solver,
            joint_seed_deg=joint_seed_deg,
            target_position_m=target_position_m,
            target_rpy_rad=target_rpy_rad,
        )
