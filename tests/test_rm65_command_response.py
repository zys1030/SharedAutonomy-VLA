from scripts.test_rm65_command_response import motion_started, summarize_ms


def test_motion_started_from_position_or_speed() -> None:
    assert motion_started(10.03, 0.0, 10.0, 0.02, 0.1)
    assert motion_started(10.0, 0.2, 10.0, 0.02, 0.1)
    assert not motion_started(10.01, 0.05, 10.0, 0.02, 0.1)


def test_summarize_ms() -> None:
    assert summarize_ms([10.0, 20.0]) == {
        "samples": 2,
        "mean_ms": 15.0,
        "median_ms": 15.0,
        "min_ms": 10.0,
        "max_ms": 20.0,
        "values_ms": [10.0, 20.0],
        "ordered_ms": [10.0, 20.0],
    }
