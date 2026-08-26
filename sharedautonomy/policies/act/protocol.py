"""JSON wire format for ACT cloud inference (observation in, action out).

Images travel as base64-encoded HWC uint8 RGB (ADR 0002 camera layout), either
raw (``encoding="base64"``) or JPEG-compressed (``encoding="jpeg_b64"``) to cut
the ~2.4MB/step payload on bandwidth-limited links.
No LeRobot / torch imports here so a thin client can encode without GPU stacks
beyond numpy (cv2 is imported lazily only on the JPEG path).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import numpy as np

ACTION_DIM = 7
# Full live catalog (ADR 0002). Wire vectors follow dataset ``names``, not a dim whitelist.
STATE_NAMES = (
    "joint_1.pos",
    "joint_2.pos",
    "joint_3.pos",
    "joint_4.pos",
    "joint_5.pos",
    "joint_6.pos",
    "gripper.pos",
    "ee.z",
    "ee.dz",
    "gripper.time_since_close",
)
STATE_DIM = len(STATE_NAMES)
DEFAULT_IMAGE_HWC = (480, 640, 3)
WRIST_KEY = "wrist"
EXTERNAL_KEY = "external"

RAW_IMAGE_ENCODING = "base64"
JPEG_IMAGE_ENCODING = "jpeg_b64"
DEFAULT_JPEG_QUALITY = 90


@dataclass(frozen=True)
class InferObservation:
    """One control-step observation for ACT."""

    state: np.ndarray  # float32 (N,) assembled from dataset observation.state names
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


def catalog_state_names(state_dim: int) -> list[str]:
    """Prefix of the live catalog; only for old servers / ``--state-dim``."""
    dim = int(state_dim)
    if dim < 1:
        raise ValueError(f"observation.state dim must be >= 1, got {dim}")
    if dim > len(STATE_NAMES):
        raise ValueError(
            f"observation.state dim {dim} exceeds the live catalog ({len(STATE_NAMES)}). "
            "Dataset must declare observation.state names."
        )
    return list(STATE_NAMES[:dim])


def coerce_state_vector(state: Any) -> np.ndarray:
    array = np.asarray(state, dtype=np.float32).reshape(-1)
    if array.size < 1:
        raise ValueError("observation.state must be a non-empty 1-D vector")
    return array


def require_state_matches_dim(
    state: Any,
    expected_dim: int,
    *,
    dataset_root: str | None = None,
) -> np.ndarray:
    expected = int(expected_dim)
    if expected < 1:
        raise ValueError(f"expected observation.state dim must be >= 1, got {expected}")
    array = coerce_state_vector(state)
    if int(array.size) != expected:
        extra = f" dataset_root={dataset_root}" if dataset_root is not None else ""
        raise ValueError(
            f"observation.state length must be {expected}, got {int(array.size)}.{extra}"
        )
    return array


def _feature_state_spec(features: Any) -> dict[str, Any] | Any:
    if not isinstance(features, dict) or "observation.state" not in features:
        raise ValueError("dataset features must include observation.state")
    return features["observation.state"]


def _feature_shape_dim(spec: Any) -> int:
    shape = spec.get("shape") if isinstance(spec, dict) else getattr(spec, "shape", None)
    if shape is None:
        raise ValueError("observation.state feature is missing shape")
    return int(shape) if isinstance(shape, int) else int(shape[0])


def _feature_state_names(spec: Any) -> list[str] | None:
    names = spec.get("names") if isinstance(spec, dict) else getattr(spec, "names", None)
    if names is None:
        return None
    parsed = [str(name) for name in names]
    return parsed or None


def observation_state_layout_from_features(features: Any) -> tuple[int, list[str]]:
    spec = _feature_state_spec(features)
    dim = _feature_shape_dim(spec)
    if dim < 1:
        raise ValueError(f"observation.state dim must be >= 1, got {dim}")
    names = _feature_state_names(spec)
    if names is None:
        names = catalog_state_names(dim)
    elif len(names) != dim:
        raise ValueError(
            f"observation.state names length {len(names)} does not match shape {dim}"
        )
    return dim, names


def observation_state_dim_from_features(features: Any) -> int:
    return observation_state_layout_from_features(features)[0]


def observation_state_layout_from_dataset(dataset: Any) -> tuple[int, list[str]]:
    meta = getattr(dataset, "meta", None)
    features = getattr(meta, "features", None)
    if features is None and isinstance(meta, dict):
        features = meta.get("features")
    return observation_state_layout_from_features(features)


def observation_state_dim_from_dataset(dataset: Any) -> int:
    return observation_state_layout_from_dataset(dataset)[0]


def validate_observation(obs: InferObservation) -> None:
    coerce_state_vector(obs.state)
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


def _encode_image_hwc_uint8(
    image: np.ndarray,
    *,
    encoding: str = RAW_IMAGE_ENCODING,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> dict[str, Any]:
    image = np.asarray(image)
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError(f"image must be HWC uint8 RGB, got dtype={image.dtype} shape={image.shape}")
    if encoding == RAW_IMAGE_ENCODING:
        data_b64 = base64.b64encode(np.ascontiguousarray(image).tobytes()).decode("ascii")
    elif encoding == JPEG_IMAGE_ENCODING:
        data_b64 = _jpeg_encode_rgb(image, jpeg_quality=jpeg_quality)
    else:
        raise ValueError(f"unknown image encoding: {encoding!r}")
    return {
        "shape": list(image.shape),
        "dtype": "uint8",
        "encoding": encoding,
        "data_b64": data_b64,
    }


def _jpeg_encode_rgb(image_rgb: np.ndarray, *, jpeg_quality: int) -> str:
    import cv2

    if not 1 <= int(jpeg_quality) <= 100:
        raise ValueError(f"jpeg_quality must be in [1, 100], got {jpeg_quality}")
    bgr = np.ascontiguousarray(image_rgb[..., ::-1])
    ok, buffer = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)])
    if not ok:
        raise RuntimeError("cv2.imencode(.jpg) failed")
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _decode_image_hwc_uint8(payload: dict[str, Any]) -> np.ndarray:
    shape = tuple(int(x) for x in payload["shape"])
    if payload.get("dtype") != "uint8":
        raise ValueError("image payload must use dtype=uint8")
    encoding = payload.get("encoding")
    raw = base64.b64decode(payload["data_b64"])
    if encoding == RAW_IMAGE_ENCODING:
        image = np.frombuffer(raw, dtype=np.uint8).reshape(shape)
    elif encoding == JPEG_IMAGE_ENCODING:
        image = _jpeg_decode_rgb(raw)
    else:
        raise ValueError(f"unknown image encoding: {encoding!r}")
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError(f"decoded image must be HWC RGB, got {image.shape}")
    if tuple(image.shape) != shape:
        raise ValueError(f"decoded image shape {tuple(image.shape)} does not match payload shape {shape}")
    return image


def _jpeg_decode_rgb(data: bytes) -> np.ndarray:
    import cv2

    buffer = np.frombuffer(data, dtype=np.uint8)
    bgr = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("cv2.imdecode failed on JPEG payload")
    return np.ascontiguousarray(bgr[..., ::-1])


def observation_to_payload(
    obs: InferObservation,
    *,
    image_encoding: str = RAW_IMAGE_ENCODING,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> dict[str, Any]:
    validate_observation(obs)
    return {
        "task": str(obs.task),
        "reset": bool(obs.reset),
        "observation": {
            "state": coerce_state_vector(obs.state).tolist(),
            "images": {
                WRIST_KEY: _encode_image_hwc_uint8(
                    obs.wrist_rgb_hwc, encoding=image_encoding, jpeg_quality=jpeg_quality
                ),
                EXTERNAL_KEY: _encode_image_hwc_uint8(
                    obs.external_rgb_hwc, encoding=image_encoding, jpeg_quality=jpeg_quality
                ),
            },
        },
    }


def payload_to_observation(payload: dict[str, Any]) -> InferObservation:
    observation = payload["observation"]
    images = observation["images"]
    obs = InferObservation(
        state=coerce_state_vector(observation["state"]),
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
