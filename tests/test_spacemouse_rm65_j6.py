import pytest

from scripts.test_spacemouse_rm65_j6 import decode_int16_le, normalize_axis


def test_decode_int16_le() -> None:
    assert decode_int16_le([2, 0, 0, 0, 0, 0x9C, 0xFF], 5) == -100
    assert decode_int16_le([2, 0, 0, 0, 0, 0x64, 0x00], 5) == 100


def test_decode_int16_le_rejects_short_report() -> None:
    with pytest.raises(ValueError, match="does not contain"):
        decode_int16_le([2, 0], 1)


def test_normalize_axis_applies_deadzone_and_rescaling() -> None:
    assert normalize_axis(0, 0.15) == 0.0
    assert normalize_axis(35, 0.15) == 0.0
    assert normalize_axis(350, 0.15) == 1.0
    assert normalize_axis(-350, 0.15) == -1.0
