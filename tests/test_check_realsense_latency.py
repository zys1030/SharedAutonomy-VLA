from scripts.check_realsense_latency import (
    is_wall_clock_comparable,
    summarize_values_ms,
)


def test_summarize_values_ms() -> None:
    assert summarize_values_ms([1.0, 2.0, 3.0, 4.0]) == {
        "samples": 4,
        "mean_ms": 2.5,
        "median_ms": 2.5,
        "p95_ms": 3.0,
        "p99_ms": 3.0,
        "min_ms": 1.0,
        "max_ms": 4.0,
    }


def test_summarize_values_ms_handles_empty_input() -> None:
    assert summarize_values_ms([]) == {"samples": 0}


def test_wall_clock_comparability() -> None:
    assert is_wall_clock_comparable(1_000_000.0, 1_000_010.0)
    assert not is_wall_clock_comparable(1_000.0, 1_000_000.0)
