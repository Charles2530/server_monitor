"""Normalization rules shared by collection, storage and the UI API."""
import math

METRICS = ("cpu", "gpu", "memory", "gpu_memory")


def finite_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def is_target(name, state, keyword="charles"):
    return keyword.casefold() in str(name).casefold() and str(state).casefold() in {
        "running", "运行中"
    }


def merge_series(*groups):
    merged = {}
    for group in groups:
        for item in group:
            key = (item["metric"], item["label"])
            points = merged.setdefault(key, {})
            for timestamp, value in item["points"]:
                points[float(timestamp)] = finite_number(value)
    return [
        {"metric": key[0], "label": key[1], "points": [[t, v] for t, v in sorted(points.items())]}
        for key, points in sorted(merged.items())
    ]


def summarize_metrics(series, now, max_age=300):
    """Mean of fresh worker/card values at the latest sample time; never sum %."""
    result = dict.fromkeys(METRICS)
    for metric in METRICS:
        candidates = []
        for item in series:
            if item["metric"] != metric or not item["points"]:
                continue
            timestamp, value = max(item["points"], key=lambda p: p[0])
            value = finite_number(value)
            if value is not None and 0 <= now - float(timestamp) <= max_age:
                candidates.append((timestamp, value))
        if candidates:
            latest = max(t for t, _ in candidates)
            values = [v for t, v in candidates if latest - t <= 60]
            result[metric] = round(sum(values) / len(values), 2)
    return result
