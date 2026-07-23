import pytest

from scripts.check_rm65_read_latency import summarize_durations


def test_summarize_durations() -> None:
    result = summarize_durations([1_000_000, 2_000_000, 3_000_000, 4_000_000])

    assert result == {
        "samples": 4,
        "mean_ms": 2.5,
        "median_ms": 2.5,
        "p95_ms": 3.0,
        "p99_ms": 3.0,
        "min_ms": 1.0,
        "max_ms": 4.0,
    }


def test_summarize_durations_handles_empty_input() -> None:
    assert summarize_durations([]) == {"samples": 0}


def test_summarize_durations_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        summarize_durations([-1])
