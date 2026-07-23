import pytest

from scripts.test_rm65_high_follow_response import summarize_ms, triangle_offset_deg


def test_triangle_offset_returns_to_zero() -> None:
    assert [triangle_offset_deg(index, 2, 0.1) for index in range(5)] == [
        0.0,
        0.05,
        0.1,
        0.05,
        0.0,
    ]


def test_triangle_offset_rejects_invalid_index() -> None:
    with pytest.raises(ValueError, match="within"):
        triangle_offset_deg(5, 2, 0.1)


def test_summarize_ms() -> None:
    result = summarize_ms([1.0, 2.0, 3.0])

    assert result["median_ms"] == 2.0
    assert result["max_ms"] == 3.0
