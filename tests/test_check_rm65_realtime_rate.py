from scripts.check_rm65_realtime_rate import summarize_intervals


def test_summarize_intervals() -> None:
    result = summarize_intervals([0, 5_000_000, 10_000_000, 15_000_000])

    assert result == {
        "samples": 4,
        "intervals": 3,
        "mean_ms": 5.0,
        "median_ms": 5.0,
        "p95_ms": 5.0,
        "p99_ms": 5.0,
        "min_ms": 5.0,
        "max_ms": 5.0,
        "over_10ms": 0,
        "over_20ms": 0,
        "effective_hz": 200.0,
    }


def test_summarize_intervals_requires_two_samples() -> None:
    assert summarize_intervals([0]) == {"samples": 1, "intervals": 0}
