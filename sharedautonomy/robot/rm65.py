"""LeRobot 0.6 adapter for a RealMan RM-65B robot arm."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from lerobot.cameras import CameraConfig, make_cameras_from_configs
from lerobot.robots import RobotConfig
from lerobot.robots.robot import Robot
from lerobot.types import RobotAction, RobotObservation

from .gripper import RealManControllerGripper
from .safety import MotionDisabledError, clip_joint_targets, validate_joint_limits

logger = logging.getLogger(__name__)

JOINT_KEYS = tuple(f"joint_{index}.pos" for index in range(1, 7))
EE_KEYS = ("ee.x", "ee.y", "ee.z", "ee.rx", "ee.ry", "ee.rz")
GRIPPER_KEY = "gripper.pos"


@RobotConfig.register_subclass("rm65")
@dataclass
class RM65Config(RobotConfig):
    """Configuration for the RM-65B hardware adapter."""

    ip: str
    port: int = 8080
    cameras: dict[str, CameraConfig] = field(default_factory=dict)
    run_mode: int = 1
    set_run_mode_on_connect: bool = False
    sdk_log_level: int = 3

    # Motion is deliberately opt-in. Official joint limits must be supplied
    # before enable_motion may be set to True.
    enable_motion: bool = False
    max_relative_target_deg: float | list[float] = 1.0
    joint_limits_deg: list[list[float]] | None = None

    # CAN-FD passthrough parameters. High-follow mode requires a stable
    # control period below 10 ms and is therefore disabled by default.
    canfd_follow: bool = False
    canfd_trajectory_mode: int = 0
    canfd_smoothing: int = 0

    # The standard controller gripper is optional because the previous setup
    # also used a separate serial soft gripper.
    use_controller_gripper: bool = False
    gripper_position_min: int = 1
    gripper_position_max: int = 1000
    gripper_block: bool = False
    gripper_timeout_s: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.ip.strip():
            raise ValueError("ip must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be in [1, 65535]")
        if self.run_mode not in {0, 1}:
            raise ValueError("run_mode must be 0 (simulation) or 1 (real)")
        if not 0 <= self.sdk_log_level <= 3:
            raise ValueError("sdk_log_level must be in [0, 3]")
        if self.canfd_trajectory_mode not in {0, 1, 2}:
            raise ValueError("canfd_trajectory_mode must be 0, 1, or 2")
        if self.canfd_smoothing < 0:
            raise ValueError("canfd_smoothing must be non-negative")
        validate_joint_limits(self.joint_limits_deg, len(JOINT_KEYS))
        if self.enable_motion and self.joint_limits_deg is None:
            raise ValueError("joint_limits_deg must be configured before enable_motion=True")


class RM65(Robot):
    """Minimal RM-65B adapter with read-only-by-default connection semantics."""

    config_class = RM65Config
    name = "rm65"

    def __init__(
        self,
        config: RM65Config,
        *,
        arm_factory: Callable[[], Any] | None = None,
        cameras_factory: Callable[[dict[str, CameraConfig]], dict[str, Any]] = make_cameras_from_configs,
    ) -> None:
        super().__init__(config)
        self.config = config
        self._arm = arm_factory() if arm_factory is not None else self._make_sdk_arm()
        self.cameras = cameras_factory(config.cameras)
        self._arm_connected = False
        self._handle_id: int | None = None
        self._gripper = (
            RealManControllerGripper(
                self._arm,
                position_min=config.gripper_position_min,
                position_max=config.gripper_position_max,
                block=config.gripper_block,
                timeout_s=config.gripper_timeout_s,
            )
            if config.use_controller_gripper
            else None
        )

    @staticmethod
    def _make_sdk_arm() -> Any:
        try:
            from Robotic_Arm.rm_robot_interface import RoboticArm, rm_thread_mode_e
        except ImportError as exc:
            raise ImportError(
                "RM-65B support requires the RealMan Robotic-Arm SDK. "
                "Install the project's hardware extra after validating its Python compatibility."
            ) from exc
        return RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)

    @property
    def _joint_features(self) -> dict[str, type]:
        return {key: float for key in JOINT_KEYS}

    @property
    def _end_effector_features(self) -> dict[str, type]:
        return {key: float for key in EE_KEYS}

    @property
    def _camera_features(self) -> dict[str, tuple[int, ...]]:
        features: dict[str, tuple[int, ...]] = {}
        for name, camera in self.cameras.items():
            if getattr(camera, "use_rgb", True):
                features[name] = (camera.height, camera.width, 3)
            if getattr(camera, "use_depth", False):
                features[f"{name}_depth"] = (camera.height, camera.width, 1)
        return features

    @cached_property
    def observation_features(self) -> dict[str, type | tuple[int, ...]]:
        features: dict[str, type | tuple[int, ...]] = {
            **self._joint_features,
            **self._end_effector_features,
            **self._camera_features,
        }
        if self._gripper is not None:
            features[GRIPPER_KEY] = float
        return features

    @cached_property
    def action_features(self) -> dict[str, type]:
        features = dict(self._joint_features)
        if self._gripper is not None:
            features[GRIPPER_KEY] = float
        return features

    @property
    def is_connected(self) -> bool:
        cameras_connected = all(bool(camera.is_connected) for camera in self.cameras.values())
        return self._arm_connected and cameras_connected

    def connect(self, calibrate: bool = True) -> None:
        """Connect without moving the arm or gripper."""
        del calibrate
        if self._arm_connected:
            raise RuntimeError("RM-65B is already connected")

        handle = self._arm.rm_create_robot_arm(
            self.config.ip,
            self.config.port,
            level=self.config.sdk_log_level,
        )
        handle_id = int(getattr(handle, "id", -1))
        if handle_id < 0:
            raise ConnectionError(f"Failed to connect to RM-65B at {self.config.ip}:{self.config.port}")

        self._handle_id = handle_id
        self._arm_connected = True
        try:
            if self.config.set_run_mode_on_connect:
                self._check_status(self._arm.rm_set_arm_run_mode(self.config.run_mode), "set arm run mode")
            for camera in self.cameras.values():
                camera.connect()
        except Exception:
            self._disconnect_quietly()
            raise

        logger.info("Connected to RM-65B handle %s without issuing a motion command", self._handle_id)

    @property
    def is_calibrated(self) -> bool:
        return True

    def calibrate(self) -> None:
        """RM-65B calibration is managed by its controller."""

    def configure(self) -> None:
        """Runtime mode is applied during connection."""

    def get_observation(self) -> RobotObservation:
        self._require_connected()
        joints = self._read_joint_positions()
        pose = self._read_end_effector_pose(joints)
        observation: RobotObservation = {
            **dict(zip(JOINT_KEYS, joints, strict=True)),
            **dict(zip(EE_KEYS, pose, strict=True)),
        }

        if self._gripper is not None:
            observation[GRIPPER_KEY] = self._gripper.get_position()

        for name, camera in self.cameras.items():
            if getattr(camera, "use_rgb", True):
                observation[name] = camera.read_latest()
            if getattr(camera, "use_depth", False):
                observation[f"{name}_depth"] = camera.read_latest_depth()
        return observation

    def send_action(self, action: RobotAction) -> RobotAction:
        self._require_connected()
        if not self.config.enable_motion:
            raise MotionDisabledError(
                "RM-65B motion is disabled. Configure official joint limits and set enable_motion=True "
                "only after completing the hardware safety checks."
            )

        missing = [key for key in self.action_features if key not in action]
        if missing:
            raise ValueError(f"RM-65B action is missing required keys: {missing}")

        requested_joints = [float(action[key]) for key in JOINT_KEYS]
        present_joints = self._read_joint_positions()
        safe_joints = clip_joint_targets(
            present_joints,
            requested_joints,
            self.config.max_relative_target_deg,
            self.config.joint_limits_deg,
        )
        gripper_command = (
            self._gripper.prepare_position(float(action[GRIPPER_KEY])) if self._gripper is not None else None
        )

        status = self._arm.rm_movej_canfd(
            safe_joints,
            follow=self.config.canfd_follow,
            expand=0,
            trajectory_mode=self.config.canfd_trajectory_mode,
            radio=self.config.canfd_smoothing,
        )
        self._check_status(status, "send CAN-FD joint target")

        executed: RobotAction = dict(zip(JOINT_KEYS, safe_joints, strict=True))
        if self._gripper is not None and gripper_command is not None:
            executed[GRIPPER_KEY] = float(self._gripper.set_position(gripper_command))
        return executed

    def disconnect(self) -> None:
        errors: list[Exception] = []
        for camera in self.cameras.values():
            if bool(getattr(camera, "is_connected", False)):
                try:
                    camera.disconnect()
                except Exception as exc:  # Hardware cleanup should continue for every device.
                    errors.append(exc)

        if self._arm_connected:
            try:
                status = self._arm.rm_delete_robot_arm()
                self._check_status(status, "delete robot arm handle")
            except Exception as exc:
                errors.append(exc)
            finally:
                self._arm_connected = False
                self._handle_id = None

        if errors:
            raise RuntimeError(f"RM-65B disconnect completed with {len(errors)} error(s)") from errors[0]
        logger.info("Disconnected RM-65B")

    def _read_joint_positions(self) -> list[float]:
        status, joints = self._arm.rm_get_joint_degree()
        self._check_status(status, "read joint positions")
        if len(joints) < len(JOINT_KEYS):
            raise RuntimeError(
                f"RM-65B SDK returned {len(joints)} joints, expected at least {len(JOINT_KEYS)}"
            )
        return [float(value) for value in joints[: len(JOINT_KEYS)]]

    def _read_end_effector_pose(self, joints: list[float]) -> list[float]:
        pose = self._arm.rm_algo_forward_kinematics(joints, flag=1)
        if len(pose) != len(EE_KEYS):
            raise RuntimeError(f"RM-65B SDK returned an invalid end-effector pose of length {len(pose)}")
        return [float(value) for value in pose]

    def solve_inverse_kinematics(
        self,
        *,
        joint_seed_deg: list[float] | tuple[float, ...],
        target_position_m: list[float] | tuple[float, ...],
        target_rpy_rad: list[float] | tuple[float, ...],
    ) -> list[float]:
        """Solve Cartesian pose to joints using the connected RealMan SDK handle."""
        self._require_connected()
        from sharedautonomy.robot.kinematics import solve_inverse_kinematics

        return solve_inverse_kinematics(
            self._arm,
            joint_seed_deg=joint_seed_deg,
            target_position_m=target_position_m,
            target_rpy_rad=target_rpy_rad,
        )

    def _require_connected(self) -> None:
        if not self.is_connected:
            raise ConnectionError("RM-65B is not connected")

    def _disconnect_quietly(self) -> None:
        try:
            self.disconnect()
        except Exception:
            logger.exception("Failed to clean up RM-65B after a connection error")

    @staticmethod
    def _check_status(status: int, operation: str) -> None:
        if status != 0:
            raise RuntimeError(f"RM-65B failed to {operation}: SDK status {status}")
