import math

from server.domain import finite_number, is_target, merge_series, summarize_metrics


def test_target_uses_name_contains_and_running_state():
    assert is_target("kill_before_notice_grh_charles_pixel_train", "Running")
    assert not is_target("someone_else", "Running")
    assert not is_target("charles_template", "Stopped")
    assert is_target("charles_dev", "RUNNING")


def test_unknown_numeric_is_not_zero():
    assert finite_number("NA") is None
    assert finite_number(None) is None
    assert finite_number("NaN") is None
    assert finite_number(float("inf")) is None
    assert finite_number("0") == 0
    assert finite_number("20.5") == 20.5


def test_summary_averages_workers_at_latest_common_time_without_summing_percent():
    series = [
        {"metric": "gpu", "label": "master-0", "points": [[100, 40], [160, 80]]},
        {"metric": "gpu", "label": "worker-0", "points": [[100, 80], [160, 100]]},
    ]
    assert summarize_metrics(series, now=180)["gpu"] == 90
    assert summarize_metrics(series, now=180)["cpu"] is None


def test_old_sample_is_missing_not_live():
    assert summarize_metrics([
        {"metric": "cpu", "label": "master-0", "points": [[100, 20]]}
    ], now=1000)["cpu"] is None


def test_merge_series_deduplicates_times_and_preserves_worker_identity():
    before = [{"metric": "cpu", "label": "worker-0", "points": [[10, 20], [20, 30]]}]
    after = [{"metric": "cpu", "label": "worker-0", "points": [[20, 35], [30, None]]}]
    assert merge_series(before, after) == [
        {"metric": "cpu", "label": "worker-0", "points": [[10, 20], [20, 35], [30, None]]}
    ]
