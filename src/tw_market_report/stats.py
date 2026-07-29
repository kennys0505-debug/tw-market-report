from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence


def finite(values: Iterable[float | int | None]) -> list[float]:
    return [float(v) for v in values if v is not None and math.isfinite(float(v))]


def percentile_rank(values: Sequence[float | int | None], value: float | None) -> float | None:
    clean = finite(values)
    if value is None or not clean:
        return None
    below = sum(v < value for v in clean)
    equal = sum(v == value for v in clean)
    return 100.0 * (below + 0.5 * equal) / len(clean)


def robust_zscore(values: Sequence[float | int | None], value: float | None, clip: float = 3.0) -> float | None:
    clean = finite(values)
    if value is None or len(clean) < 5:
        return None
    median = statistics.median(clean)
    deviations = [abs(v - median) for v in clean]
    mad = statistics.median(deviations)
    if mad == 0:
        return 0.0
    z = 0.67448975 * (float(value) - median) / mad
    return max(-clip, min(clip, z))


def pearson(a: Sequence[float | None], b: Sequence[float | None]) -> float | None:
    pairs = [(float(x), float(y)) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 20:
        return None
    xs, ys = zip(*pairs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denominator = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    return sum(x * y for x, y in zip(dx, dy)) / denominator if denominator else 0.0


def mean(values: Iterable[float | int | None], default: float = 0.0) -> float:
    clean = finite(values)
    return statistics.fmean(clean) if clean else default


def safe_div(numerator: float | int | None, denominator: float | int | None, default: float = 0.0) -> float:
    if numerator is None or denominator in (None, 0):
        return default
    return float(numerator) / float(denominator)


def forward_return(history: Sequence[dict], index: int, days: int, key: str = "taiex_close") -> float | None:
    target = index + days
    if target >= len(history):
        return None
    start, end = history[index].get(key), history[target].get(key)
    if not start or end is None:
        return None
    return float(end) / float(start) - 1.0

