"""JSON wire format for ACT cloud inference (observation in, action out).

Images travel as base64-encoded HWC uint8 RGB (ADR 0002 camera layout).
No LeRobot / torch imports here so a thin client can encode without GPU stacks
beyond numpy.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import numpy as np

ACTION_DIM = 7
STATE_DIM = 7
DEFAULT_IMAGE_HWC = (480, 640, 3)
WRIST_KEY = "wrist"
EXTERNAL_KEY = "external"


@dataclass(frozen=True)
class InferObservation:
    """One control-step observation for ACT."""

    state: np.ndarray  # float32 (7,)
    wrist_rgb_hwc: np.ndarray  # uint8 (H, W, 3)
    external_rgb_hwc: np.ndarray  # uint8 (H, W, 3)
    task: str
    reset: bool = False


@dataclass(frozen=True)
class InferResponse:
    """Single-step action returned by the server (after ACT select_action)."""

    action: np.ndarray  # float32 (7,) joint deg x6 + gripper [0,1]
    chunk_size: int | None = None
    n_action_steps: int | None = None


def _require_shape(name: str, array: np.ndarray, shape: tuple[int, ...]) -> None:
    if tuple(array.shape) != shape:
        raise ValueError(f"{name} shape must be {shape}, got {tuple(array.shape)}")


def validate_observation(obs: InferObservation) -> None:
    state = np.asarray(obs.state, dtype=np.float32)
    _require_shape("state", state, (STATE_DIM,))
    wrist = np.asarray(obs.wrist_rgb_hwc)
    external = np.asarray(obs.external_rgb_hwc)
    if wrist.dtype != np.uint8 or external.dtype != np.uint8:
        raise ValueError("wrist/external images must be uint8 RGB")
    if wrist.ndim != 3 or wrist.shape[-1] != 3:
        raise ValueError(f"wrist image must be HWC RGB, got {wrist.shape}")
    if external.ndim != 3 or external.shape[-1] != 3:
        raise ValueError(f"external image must be HWC RGB, got {external.shape}")
    if not obs.task or not str(obs.task).strip():
        raise ValueError("task must be a non-empty string")


def _encode_image_hwc_uint8(image: np.ndarray) -> dict[str, Any]:
    image = np.asarray(image)
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError(f"image must be HWC uint8 RGB, got dtype={image.dtype} shape={image.shape}")
    return {
        "shape": list(image.shape),
        "dtype": "uint8",
        "encoding": "base64",
        "data_b64": base64.b64encode(np.ascontiguousarray(image).tobytes()).decode("ascii"),
    }


def _decode_image_hwc_uint8(payload: dict[str, Any]) -> np.ndarray:
    shape = tuple(int(x) for x in payload["shape"])
    if payload.get("dtype") != "uint8" or payload.get("encoding") != "base64":
        raise ValueError("image payload must use dtype=uint8 and encoding=base64")
    raw = base64.b64decode(payload["data_b64"])
    image = np.frombuffer(raw, dtype=np.uint8).reshape(shape)
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError(f"decoded image must be HWC RGB, got {image.shape}")
    return image


def observation_to_payload(obs: InferObservation) -> dict[str, Any]:
    validate_observation(obs)
    return {
        "task": str(obs.task),
        "reset": bool(obs.reset),
        "observation": {
            "state": np.asarray(obs.state, dtype=np.float32).reshape(STATE_DIM).tolist(),
            "images": {
                WRIST_KEY: _encode_image_hwc_uint8(obs.wrist_rgb_hwc),
                EXTERNAL_KEY: _encode_image_hwc_uint8(obs.external_rgb_hwc),
            },
        },
    }


def payload_to_observation(payload: dict[str, Any]) -> InferObservation:
    observation = payload["observation"]
    images = observation["images"]
    obs = InferObservation(
        state=np.asarray(observation["state"], dtype=np.float32).reshape(STATE_DIM),
        wrist_rgb_hwc=_decode_image_hwc_uint8(images[WRIST_KEY]),
        external_rgb_hwc=_decode_image_hwc_uint8(images[EXTERNAL_KEY]),
        task=str(payload["task"]),
        reset=bool(payload.get("reset", False)),
    )
    validate_observation(obs)
    return obs


def response_to_payload(response: InferResponse) -> dict[str, Any]:
    action = np.asarray(response.action, dtype=np.float32).reshape(ACTION_DIM)
    payload: dict[str, Any] = {
        "action": action.tolist(),
        "action_names": [
            "joint_1.pos",
            "joint_2.pos",
            "joint_3.pos",
            "joint_4.pos",
            "joint_5.pos",
            "joint_6.pos",
            "gripper.pos",
        ],
        "units": {
            "joint_1.pos": "deg",
            "joint_2.pos": "deg",
            "joint_3.pos": "deg",
            "joint_4.pos": "deg",
            "joint_5.pos": "deg",
            "joint_6.pos": "deg",
            "gripper.pos": "open_fraction",
        },
    }
    if response.chunk_size is not None:
        payload["chunk_size"] = int(response.chunk_size)
    if response.n_action_steps is not None:
        payload["n_action_steps"] = int(response.n_action_steps)
    return payload


def payload_to_response(payload: dict[str, Any]) -> InferResponse:
    action = np.asarray(payload["action"], dtype=np.float32).reshape(ACTION_DIM)
    return InferResponse(
        action=action,
        chunk_size=payload.get("chunk_size"),
        n_action_steps=payload.get("n_action_steps"),
    )


def chw_float_to_hwc_uint8(image_chw: np.ndarray) -> np.ndarray:
    """Convert LeRobot-style CHW float image in [0, 1] (or [0, 255]) to HWC uint8."""
    image = np.asarray(image_chw)
    if image.ndim != 3 or image.shape[0] not in (1, 3):
        raise ValueError(f"expected CHW image, got shape={image.shape}")
    hwc = np.transpose(image, (1, 2, 0))
    if np.issubdtype(hwc.dtype, np.floating):
        max_val = float(np.max(hwc)) if hwc.size else 0.0
        if max_val <= 1.0 + 1e-3:
            hwc = hwc * 255.0
        hwc = np.clip(hwc, 0.0, 255.0)
    return np.asarray(hwc, dtype=np.uint8)
