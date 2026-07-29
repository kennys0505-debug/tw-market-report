from __future__ import annotations

from statistics import fmean, median
from typing import Any

from .stats import forward_return, percentile_rank


HORIZONS = (1, 5, 10, 20)


def _path_stats(history: list[dict[str, Any]], indexes: list[int], horizon: int) -> dict[str, Any]:
    returns: list[float] = []
    favorable: list[float] = []
    adverse: list[float] = []
    for index in indexes:
        result = forward_return(history, index, horizon)
        start = history[index].get("taiex_close")
        if result is None or not start:
            continue
        path = [
            float(history[target]["taiex_close"]) / float(start) - 1.0
            for target in range(index + 1, min(index + horizon + 1, len(history)))
            if history[target].get("taiex_close") is not None
        ]
        returns.append(result)
        if path:
            favorable.append(max(path))
            adverse.append(min(path))
    return {
        "samples": len(returns),
        "average_return": fmean(returns) if returns else None,
        "median_return": median(returns) if returns else None,
        "up_probability": sum(value > 0 for value in returns) / len(returns) if returns else None,
        "max_favorable_excursion": fmean(favorable) if favorable else None,
        "max_adverse_excursion": fmean(adverse) if adverse else None,
    }


def run_limit_backtest(history: list[dict[str, Any]], minimum_samples: int = 30) -> dict[str, Any]:
    baseline: list[int] = []
    augmented: list[int] = []
    horizon_signals: dict[str, list[int]] = {"1d": [], "3d": [], "5d": []}
    ratio_series = [float(row.get("limit_down_ratio") or 0.0) for row in history]
    for index, row in enumerate(history):
        if index < 60 or row.get("limit_down_ratio") is None:
            continue
        prior = [item.get("limit_down_ratio") for item in history[max(0, index - 1260):index]]
        percentile = percentile_rank(prior, float(row["limit_down_ratio"]))
        baseline_signal = float(row.get("composite_score") or 50.0) <= 30.0
        extreme = percentile is not None and percentile >= 95.0
        contraction = index > 0 and float(row.get("limit_down_ratio") or 0.0) <= float(history[index - 1].get("limit_down_ratio") or 0.0) * 0.5
        if baseline_signal:
            baseline.append(index)
        if baseline_signal or (extreme and contraction):
            augmented.append(index)
        for window, label in ((1, "1d"), (3, "3d"), (5, "5d")):
            if index + 1 < window:
                continue
            current_sum = sum(ratio_series[index - window + 1:index + 1])
            start = max(window - 1, index - 1260)
            prior_sums = [sum(ratio_series[j - window + 1:j + 1]) for j in range(start, index)]
            rolling_percentile = percentile_rank(prior_sums, current_sum)
            if rolling_percentile is not None and rolling_percentile >= 95.0:
                horizon_signals[label].append(index)

    baseline_10d = _path_stats(history, baseline, 10)
    augmented_10d = _path_stats(history, augmented, 10)
    baseline_hit = baseline_10d["up_probability"]
    augmented_hit = augmented_10d["up_probability"]
    improvement = None if baseline_hit is None or augmented_hit is None else (augmented_hit - baseline_hit) * 100.0
    enough = baseline_10d["samples"] >= minimum_samples and augmented_10d["samples"] >= minimum_samples
    return {
        "method": "rolling downside-extreme reversal study",
        "minimum_samples": minimum_samples,
        "baseline_10d": baseline_10d,
        "limit_augmented_10d": augmented_10d,
        "hit_rate_improvement_pp": improvement,
        "limit_scoring_enabled": bool(enough and improvement is not None and improvement >= 2.0),
        "diagnostic_only_reason": None if enough else "insufficient validated samples",
        "limit_horizon_tests": {
            name: {str(horizon): _path_stats(history, indexes, horizon) for horizon in HORIZONS}
            for name, indexes in horizon_signals.items()
        },
    }
