from __future__ import annotations

import statistics
from typing import Any

from .models import LimitStats
from .stats import forward_return, percentile_rank, safe_div


EXCLUDED_SECURITY_TYPES = {"ETF", "ETN", "WARRANT", "DR", "PREFERRED", "RIGHT", "BOND"}


def is_eligible_stock(row: dict[str, Any]) -> bool:
    security_type = str(row.get("security_type", "COMMON")).upper()
    return (
        security_type not in EXCLUDED_SECURITY_TYPES
        and bool(row.get("tradable", True))
        and bool(row.get("has_price_limit", True))
        and row.get("close") is not None
    )


def _same_price(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return abs(float(left) - float(right)) <= max(1e-9, abs(float(right)) * 1e-8)


def calculate_limit_stats(rows: list[dict[str, Any]], market: str) -> LimitStats:
    eligible = [row for row in rows if is_eligible_stock(row)]
    up = [row for row in eligible if _same_price(row.get("close"), row.get("limit_up_price"))]
    down = [row for row in eligible if _same_price(row.get("close"), row.get("limit_down_price"))]
    intraday_up = [
        row for row in eligible
        if _same_price(row.get("high"), row.get("limit_up_price"))
        and not _same_price(row.get("close"), row.get("limit_up_price"))
    ]
    intraday_down = [
        row for row in eligible
        if _same_price(row.get("low"), row.get("limit_down_price"))
        and not _same_price(row.get("close"), row.get("limit_down_price"))
    ]
    count = len(eligible)
    return LimitStats(
        market=market,
        eligible_count=count,
        limit_up_count=len(up),
        limit_down_count=len(down),
        intraday_up_touch_count=len(intraday_up),
        intraday_down_touch_count=len(intraday_down),
        limit_up_ratio=safe_div(len(up), count),
        limit_down_ratio=safe_div(len(down), count),
        limit_breadth=safe_div(len(up) - len(down), count),
        strength_ratio=(len(up) + 1) / (len(down) + 1),
        universe_verified=True,
        calculation_method="official_per_security_limit_price",
    )


def combine_limit_stats(parts: list[LimitStats]) -> LimitStats:
    combined = LimitStats(market="combined")
    for part in parts:
        combined.eligible_count += part.eligible_count
        combined.limit_up_count += part.limit_up_count
        combined.limit_down_count += part.limit_down_count
    up_touches = [part.intraday_up_touch_count for part in parts]
    down_touches = [part.intraday_down_touch_count for part in parts]
    combined.intraday_up_touch_count = sum(up_touches) if up_touches and all(value is not None for value in up_touches) else None
    combined.intraday_down_touch_count = sum(down_touches) if down_touches and all(value is not None for value in down_touches) else None
    combined.limit_up_ratio = safe_div(combined.limit_up_count, combined.eligible_count)
    combined.limit_down_ratio = safe_div(combined.limit_down_count, combined.eligible_count)
    combined.limit_breadth = safe_div(combined.limit_up_count - combined.limit_down_count, combined.eligible_count)
    combined.strength_ratio = (combined.limit_up_count + 1) / (combined.limit_down_count + 1)
    combined.universe_verified = bool(parts) and all(part.universe_verified for part in parts)
    combined.calculation_method = "combined_verified" if combined.universe_verified else "combined_mixed_scope"
    return combined


def attach_limit_percentiles(current: LimitStats, history: list[dict[str, Any]]) -> None:
    def value(row: dict[str, Any], key: str) -> Any:
        nested = row.get("limits", {}).get(current.market, {})
        return nested.get(key) if nested else row.get(key) if current.market == "combined" else None

    ratios_up = [value(row, "limit_up_ratio") for row in history]
    ratios_down = [value(row, "limit_down_ratio") for row in history]
    current.up_percentile_1y = percentile_rank(ratios_up[-252:], current.limit_up_ratio)
    current.down_percentile_1y = percentile_rank(ratios_down[-252:], current.limit_down_ratio)
    current.up_percentile_5y = percentile_rank(ratios_up[-1260:], current.limit_up_ratio)
    current.down_percentile_5y = percentile_rank(ratios_down[-1260:], current.limit_down_ratio)
    current.up_percentile_full = percentile_rank(ratios_up, current.limit_up_ratio)
    current.down_percentile_full = percentile_rank(ratios_down, current.limit_down_ratio)
    clean_up = sorted((float(item) for item in ratios_up if item is not None), reverse=True)
    clean_down = sorted((float(item) for item in ratios_down if item is not None), reverse=True)
    current.up_historical_rank = 1 + sum(item > current.limit_up_ratio for item in clean_up) if clean_up else None
    current.down_historical_rank = 1 + sum(item > current.limit_down_ratio for item in clean_down) if clean_down else None


def historical_analogs(current: LimitStats, history: list[dict[str, Any]], limit: int = 10) -> tuple[list[dict], dict]:
    candidates: list[tuple[float, int, dict]] = []
    for index, row in enumerate(history):
        nested = row.get("limits", {}).get(current.market, {})
        up_ratio = nested.get("limit_up_ratio") if nested else row.get("limit_up_ratio") if current.market == "combined" else None
        down_ratio = nested.get("limit_down_ratio") if nested else row.get("limit_down_ratio") if current.market == "combined" else None
        if up_ratio is None or down_ratio is None:
            continue
        distance = abs(float(up_ratio) - current.limit_up_ratio) + abs(float(down_ratio) - current.limit_down_ratio)
        candidates.append((distance, index, {**row, "_limit": nested or row}))
    selected = sorted(candidates, key=lambda item: (item[0], item[2].get("trade_date", "")))[:limit]
    analogs: list[dict] = []
    returns: dict[str, list[float]] = {"1d": [], "5d": [], "10d": [], "20d": []}
    for distance, index, row in selected:
        analog = {
            "market": current.market,
            "trade_date": row.get("trade_date"),
            "limit_up_count": row["_limit"].get("limit_up_count"),
            "limit_down_count": row["_limit"].get("limit_down_count"),
            "limit_up_ratio": row["_limit"].get("limit_up_ratio"),
            "limit_down_ratio": row["_limit"].get("limit_down_ratio"),
            "distance": round(distance, 6),
        }
        for days in (1, 5, 10, 20):
            value = forward_return(history, index, days)
            analog[f"return_{days}d"] = value
            if value is not None:
                returns[f"{days}d"].append(value)
        analogs.append(analog)
    summary: dict[str, Any] = {}
    for window, values in returns.items():
        summary[window] = {
            "sample_count": len(values),
            "average": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "positive_rate": safe_div(sum(v > 0 for v in values), len(values)) if values else None,
            "worst": min(values) if values else None,
        }
    return analogs, summary
