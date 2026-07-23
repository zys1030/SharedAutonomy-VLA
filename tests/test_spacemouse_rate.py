import pytest

from scripts.check_spacemouse_rate import (
    enumerate_spacemice,
    summarize_ages,
    summarize_timestamps,
)


def test_summarize_timestamps_reports_125_hz() -> None:
    result = summarize_timestamps([0, 8_000_000, 16_000_000])

    assert result["reports"] == 3
    assert result["mean_interval_ms"] == 8.0
    assert result["effective_report_hz"] == 125.0


def test_enumerate_spacemice_filters_and_deduplicates_paths() -> None:
    class FakeHid:
        @staticmethod
        def enumerate():
            return [
                {"vendor_id": 0x256F, "product_id": 0xC635, "path": b"space-mouse"},
                {"vendor_id": 0x256F, "product_id": 0xC635, "path": b"space-mouse"},
                {"vendor_id": 0x046D, "product_id": 0xC534, "path": b"ordinary-mouse"},
            ]

    assert enumerate_spacemice(FakeHid()) == [
        {"vendor_id": 0x256F, "product_id": 0xC635, "path": b"space-mouse"}
    ]


def test_summarize_ages() -> None:
    assert summarize_ages([1_000_000, 2_000_000, 3_000_000]) == {
        "samples": 3,
        "mean_ms": 2.0,
        "median_ms": 2.0,
        "p95_ms": 2.0,
        "p99_ms": 2.0,
        "min_ms": 1.0,
        "max_ms": 3.0,
    }


def test_summarize_ages_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        summarize_ages([-1])
