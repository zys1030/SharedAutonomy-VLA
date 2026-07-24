"""Robot hardware interfaces and safety boundaries."""

from .canfd_commander import RealManCanfdJointCommander
from .gripper import (
    GripperDirection,
    RealManControllerGripper,
    SerialSoftGripper,
    SerialSoftGripperConfig,
)
from .kinematics import (
    InverseKinematicsError,
    RealManInverseKinematics,
    create_rm65_offline_algo,
    solve_inverse_kinematics,
)
from .realtime_state import (
    CachedJointState,
    RealManRealtimeStateSource,
    RealtimeArmSnapshot,
    RealtimeStateError,
    UdpJointStateCache,
)
from .safety import (
    CartesianSafetyError,
    CartesianWorkspace,
    MotionDisabledError,
    clip_joint_targets,
    limit_cartesian_target,
    validate_cartesian_segment,
    validate_fixed_orientation,
    validate_signal_age,
)

__all__ = [
    "CachedJointState",
    "CartesianSafetyError",
    "CartesianWorkspace",
    "GripperDirection",
    "InverseKinematicsError",
    "MotionDisabledError",
    "RealManCanfdJointCommander",
    "RealManControllerGripper",
    "RealManInverseKinematics",
    "RealManRealtimeStateSource",
    "RealtimeArmSnapshot",
    "RealtimeStateError",
    "SerialSoftGripper",
    "SerialSoftGripperConfig",
    "UdpJointStateCache",
    "clip_joint_targets",
    "create_rm65_offline_algo",
    "limit_cartesian_target",
    "solve_inverse_kinematics",
    "validate_cartesian_segment",
    "validate_fixed_orientation",
    "validate_signal_age",
]
