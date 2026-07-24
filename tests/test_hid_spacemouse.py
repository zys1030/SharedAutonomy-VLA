"""Unit tests for HID SpaceMouse helpers and report ingest."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.core

from sharedautonomy.devices.spacemouse import (
    HidSpaceMouse,
    SpaceMouseConfig,
    decode_int16_le,
    normalize_axis,
)


def test_hid_spacemouse_ingest_translation_rotation_and_deadman() -> None:
    device = HidSpaceMouse(SpaceMouseConfig(deadzone=0.0, mount_orientation="custom"))
    # Identity mount so Compact-LEGACY signed axes stay visible after mapping.
    device.config = SpaceMouseConfig(
        deadzone=0.0,
        mount_orientation="custom",
        translation_transform=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        rotation_transform=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        max_linear_speed_m_s=0.05,
    )

    # Fake connected reader thread so read_axes is allowed.
    device._thread = object()  # type: ignore[assignment]
    # HID +X=+350, +Y=+350, +Z=+350 → Compact LEGACY ( +1, -1, -1 )
    device._ingest_report(
        [1, 0x5E, 0x01, 0x5E, 0x01, 0x5E, 0x01],
        received_monotonic_ns=1_000_000,
    )
    device._ingest_report([2, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], received_monotonic_ns=1_100_000)
    device._ingest_report([3, 0x01], received_monotonic_ns=1_200_000)

    axes = device.read_axes(now_monotonic_ns=1_500_000)
    assert axes.deadman_active is True
    assert axes.translation == pytest.approx((1.0, -1.0, -1.0))
    assert axes.input_age_ms == pytest.approx(0.5)  # older stamp is translation at 1.0ms

    action = device.read_human_action(now_monotonic_ns=1_500_000)
    assert action.deadman_active is True
    assert action.linear_velocity_m_s == pytest.approx((0.05, -0.05, -0.05))


def test_compact_legacy_signs_match_vertical_up_try_sc() -> None:
    from sharedautonomy.devices.spacemouse import (
        compact_hid_translation_to_legacy,
        map_raw_axes_to_base,
    )

    # Raw HID (+1,+1,+1) must take Compact LEGACY signs before vertical_up.
    legacy = compact_hid_translation_to_legacy(1.0, 1.0, 1.0)
    assert legacy == (1.0, -1.0, -1.0)
    translation, _ = map_raw_axes_to_base(
        legacy,
        (0.0, 0.0, 0.0),
        config=SpaceMouseConfig(deadzone=0.0, mount_orientation="vertical_up"),
    )
    assert translation.tolist() == pytest.approx([-1.0, 1.0, -1.0])


def test_base_xy_yaw_rotates_xy_keeps_z() -> None:
    from sharedautonomy.devices.spacemouse import map_raw_axes_to_base

    base, _ = map_raw_axes_to_base(
        (1.0, -1.0, -1.0),
        (0.0, 0.0, 0.0),
        config=SpaceMouseConfig(deadzone=0.0, mount_orientation="vertical_up", base_xy_yaw_deg=0.0),
    )
    yawed, _ = map_raw_axes_to_base(
        (1.0, -1.0, -1.0),
        (0.0, 0.0, 0.0),
        config=SpaceMouseConfig(deadzone=0.0, mount_orientation="vertical_up", base_xy_yaw_deg=90.0),
    )
    # +90 deg about +Z: (x, y, z) -> (-y, x, z)
    assert yawed.tolist() == pytest.approx([-base[1], base[0], base[2]])


def test_lock_z_zeros_base_z_only() -> None:
    from sharedautonomy.devices.spacemouse import map_raw_axes_to_base

    locked, _ = map_raw_axes_to_base(
        (1.0, -1.0, -1.0),
        (0.0, 0.0, 0.0),
        config=SpaceMouseConfig(
            deadzone=0.0,
            mount_orientation="vertical_up",
            base_xy_yaw_deg=90.0,
            lock_z=True,
        ),
    )
    assert locked[2] == pytest.approx(0.0)
    assert abs(locked[0]) + abs(locked[1]) > 0.0


def test_decode_int16_le_and_normalize_axis() -> None:
    assert decode_int16_le([2, 0x9C, 0xFF], 1) == -100
    assert normalize_axis(0, 0.15) == 0.0
    assert normalize_axis(35, 0.15) == 0.0
    assert normalize_axis(350, 0.15) == 1.0
    assert normalize_axis(-350, 0.15) == -1.0
