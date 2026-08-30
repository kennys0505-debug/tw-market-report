from __future__ import annotations

import math
from typing import Any

from .stats import mean


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def _sma(values: list[float], days: int) -> float | None:
    return mean(values[-days:]) if len(values) >= days else None


def _ema(values: list[float], days: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (days + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def _rsi(values: list[float], days: int = 14) -> float | None:
    if len(values) <= days:
        return None
    changes = [right - left for left, right in zip(values[-days - 1 :], values[-days:])]
    gains = mean([max(change, 0.0) for change in changes], 0.0)
    losses = mean([max(-change, 0.0) for change in changes], 0.0)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _range_position(values: list[float], days: int) -> float | None:
    if len(values) < days:
        return None
    window = values[-days:]
    low, high = min(window), max(window)
    return 50.0 if high == low else 100.0 * (values[-1] - low) / (high - low)


def _bounded(value: float) -> float:
    return max(0.0, min(100.0, value))


def _weighted(parts: list[tuple[float | None, float]]) -> tuple[float, float]:
    observed = [(value, weight) for value, weight in parts if value is not None]
    if not observed:
        return 50.0, 0.0
    observed_weight = sum(weight for _, weight in observed)
    total_weight = sum(weight for _, weight in parts)
    return (
        sum(float(value) * weight for value, weight in observed) / observed_weight,
        observed_weight / total_weight if total_weight else 0.0,
    )


def _series(history: list[dict[str, Any]], market: str) -> tuple[list[float], list[float]]:
    closes: list[float] = []
    turnovers: list[float] = []
    for row in history:
        features = row.get("features", {})
        close = row.get("taiex_close") if market == "taiex" else features.get("otc_close")
        turnover = features.get("market_turnover" if market == "taiex" else "otc_turnover")
        numeric_close = _number(close)
        if numeric_close is not None:
            closes.append(numeric_close)
        numeric_turnover = _number(turnover)
        if numeric_turnover is not None:
            turnovers.append(numeric_turnover)
    return closes, turnovers


def _index_analysis(market: str, current: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    label = "加權指數" if market == "taiex" else "櫃買指數"
    close_key = "taiex_close" if market == "taiex" else "otc_close"
    turnover_key = "market_turnover" if market == "taiex" else "otc_turnover"
    closes, turnovers = _series(history, market)
    close = _number(current.get(close_key))
    if close is None:
        return {
            "market": market,
            "label": label,
            "score": 50.0,
            "signal": "資料不足",
            "coverage": 0.0,
            "components": {},
            "reasons": [f"缺少{label}收盤價"],
        }
    values = closes + [close]
    ma = {days: _sma(values, days) for days in (20, 60, 120, 240)}
    slopes: dict[int, float | None] = {}
    for days in (20, 60, 120):
        previous = _sma(values[:-5], days)
        slopes[days] = None if ma[days] is None or previous in (None, 0) else ma[days] / previous - 1.0

    position_parts = [
        (None if ma[20] is None else (100.0 if close > ma[20] else 0.0), 0.22),
        (None if ma[60] is None else (100.0 if close > ma[60] else 0.0), 0.22),
        (None if ma[120] is None else (100.0 if close > ma[120] else 0.0), 0.14),
        (None if slopes[20] is None else _bounded(50.0 + 2500.0 * slopes[20]), 0.18),
        (None if slopes[60] is None else _bounded(50.0 + 2500.0 * slopes[60]), 0.14),
        (None if ma[20] is None or ma[60] is None else (100.0 if ma[20] > ma[60] else 0.0), 0.10),
    ]
    trend, trend_coverage = _weighted(position_parts)

    structure, structure_coverage = _weighted([
        (_range_position(values, 20), 0.6),
        (_range_position(values, 60), 0.4),
    ])

    rsi = _rsi(values)
    macd_score = None
    histogram_rising = None
    if len(values) >= 35:
        fast, slow = _ema(values, 12), _ema(values, 26)
        macd = [left - right for left, right in zip(fast, slow)]
        signal_line = _ema(macd, 9)
        histogram = [left - right for left, right in zip(macd, signal_line)]
        scale = max(abs(value) for value in histogram[-20:]) or 1.0
        macd_score = _bounded(50.0 + 35.0 * histogram[-1] / scale + (15.0 if macd[-1] > signal_line[-1] else -15.0))
        histogram_rising = len(histogram) >= 3 and histogram[-1] > histogram[-2] > histogram[-3]
    roc5 = close / values[-6] - 1.0 if len(values) >= 6 and values[-6] else None
    roc20 = close / values[-21] - 1.0 if len(values) >= 21 and values[-21] else None
    momentum, momentum_coverage = _weighted([
        (rsi, 0.35),
        (macd_score, 0.35),
        (None if roc5 is None else _bounded(50.0 + 500.0 * roc5), 0.15),
        (None if roc20 is None else _bounded(50.0 + 250.0 * roc20), 0.15),
    ])

    current_turnover = _number(current.get(turnover_key))
    average_turnover = _sma(turnovers + ([current_turnover] if current_turnover is not None else []), 20)
    daily_return = close / values[-2] - 1.0 if len(values) >= 2 and values[-2] else None
    volume_ratio = None if current_turnover is None or average_turnover in (None, 0) else current_turnover / average_turnover
    volume_score = None
    if daily_return is not None and volume_ratio is not None:
        direction = 1.0 if daily_return > 0 else -1.0 if daily_return < 0 else 0.0
        volume_score = _bounded(50.0 + direction * min(volume_ratio, 2.0) * 25.0)
    volume, volume_coverage = _weighted([(volume_score, 1.0)])

    open_price = _number(current.get(f"{market}_open"))
    high = _number(current.get(f"{market}_high"))
    low = _number(current.get(f"{market}_low"))
    candle_score = None
    if None not in (open_price, high, low) and high != low:
        body = (close - float(open_price)) / (float(high) - float(low))
        close_location = (close - float(low)) / (float(high) - float(low))
        candle_score = _bounded(50.0 + body * 30.0 + (close_location - 0.5) * 40.0)
    candle, candle_coverage = _weighted([(candle_score, 1.0)])

    components = {
        "trend": round(trend, 1),
        "structure": round(structure, 1),
        "momentum": round(momentum, 1),
        "volume": round(volume, 1),
        "candlestick_volatility": round(candle, 1),
    }
    score = 0.30 * trend + 0.25 * structure + 0.20 * momentum + 0.15 * volume + 0.10 * candle
    coverage = 0.30 * trend_coverage + 0.25 * structure_coverage + 0.20 * momentum_coverage + 0.15 * volume_coverage + 0.10 * candle_coverage

    bullish = sum([
        ma[20] is not None and close > ma[20],
        ma[60] is not None and close > ma[60],
        slopes[20] is not None and slopes[20] > 0,
        rsi is not None and rsi >= 50,
        bool(histogram_rising),
        structure >= 60,
    ])
    bearish = sum([
        ma[20] is not None and close < ma[20],
        ma[60] is not None and close < ma[60],
        slopes[20] is not None and slopes[20] < 0,
        rsi is not None and rsi < 50,
        histogram_rising is False,
        structure <= 40,
    ])
    bull_alignment = ma[20] is not None and ma[60] is not None and ma[120] is not None and close > ma[20] > ma[60] > ma[120]
    bear_alignment = ma[20] is not None and ma[60] is not None and ma[120] is not None and close < ma[20] < ma[60] < ma[120]
    if score >= 70 and bullish >= 4 and bull_alignment:
        signal = "強多"
    elif score >= 58 and bullish >= 3:
        signal = "轉多"
    elif score <= 30 and bearish >= 4 and bear_alignment:
        signal = "強空"
    elif score <= 42 and bearish >= 3:
        signal = "轉空"
    else:
        signal = "盤整"

    prior10 = values[-11:-1] if len(values) >= 11 else values[:-1]
    confirmation = None
    invalidation = None
    if prior10:
        if signal in {"轉多", "強多"}:
            confirmation = max(prior10)
            candidates = [value for value in (min(prior10), ma[20]) if value is not None]
            invalidation = min(candidates) if candidates else None
        elif signal in {"轉空", "強空"}:
            confirmation = min(prior10)
            candidates = [value for value in (max(prior10), ma[20]) if value is not None]
            invalidation = max(candidates) if candidates else None
        else:
            confirmation = max(prior10)
            invalidation = min(prior10)

    reasons: list[str] = []
    if ma[20] is not None:
        reasons.append(f"收盤{'站上' if close >= ma[20] else '跌破'}20日線")
    if ma[60] is not None:
        reasons.append(f"收盤{'站上' if close >= ma[60] else '跌破'}60日線")
    if rsi is not None:
        reasons.append(f"RSI14 {rsi:.1f}")
    if volume_ratio is not None:
        reasons.append(f"成交值為20日均量{volume_ratio:.2f}倍")
    return {
        "market": market,
        "label": label,
        "close": round(close, 2),
        "score": round(_bounded(score), 1),
        "signal": signal,
        "coverage": round(coverage, 3),
        "components": components,
        "moving_averages": {str(days): round(value, 2) if value is not None else None for days, value in ma.items()},
        "rsi14": round(rsi, 1) if rsi is not None else None,
        "roc5": roc5,
        "roc20": roc20,
        "volume_ratio_20d": round(volume_ratio, 2) if volume_ratio is not None else None,
        "confirmation_level": round(confirmation, 2) if confirmation is not None else None,
        "invalidation_level": round(invalidation, 2) if invalidation is not None else None,
        "reasons": reasons,
    }


def technical_analysis(current: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    taiex = _index_analysis("taiex", current, history)
    otc = _index_analysis("otc", current, history)
    available = [item for item in (taiex, otc) if item["coverage"] > 0]
    if len(available) == 2:
        score = taiex["score"] * 4.0 / 7.0 + otc["score"] * 3.0 / 7.0
    elif available:
        score = available[0]["score"]
    else:
        score = 50.0
    signals = {taiex["signal"], otc["signal"]}
    if score >= 70 and signals <= {"強多", "轉多"} and "強多" in signals:
        state = "強多"
    elif score >= 58 and signals & {"強多", "轉多"} and "強空" not in signals:
        state = "轉多"
    elif score <= 30 and signals <= {"強空", "轉空"} and "強空" in signals:
        state = "強空"
    elif score <= 42 and signals & {"強空", "轉空"} and "強多" not in signals:
        state = "轉空"
    else:
        state = "盤整"
    if taiex["signal"] == otc["signal"]:
        synchrony = "同向確認"
    elif signals & {"強多", "轉多"} and signals & {"強空", "轉空"}:
        synchrony = "多空分歧"
    else:
        synchrony = "尚未同步"
    if state in {"轉多", "轉空"}:
        stage = "初步轉折"
    elif state in {"強多", "強空"} and synchrony == "同向確認":
        stage = "轉折確認"
    else:
        stage = "觀察"
    return {
        "score": round(_bounded(score), 1),
        "state": state,
        "stage": stage,
        "synchrony": synchrony,
        "coverage": round(mean([item["coverage"] for item in available], 0.0), 3),
        "taiex": taiex,
        "otc": otc,
        "method": "加權指數4/7＋櫃買指數3/7；趨勢30%、結構25%、動能20%、量價15%、K線與波動10%",
    }


def auxiliary_adjustment(module_scores: dict[str, float], module_weights: dict[str, float]) -> tuple[float, float]:
    auxiliary = {key: value for key, value in module_scores.items() if key != "trend_breadth"}
    total_weight = sum(module_weights.get(key, 0.0) for key in auxiliary)
    score = (
        sum(value * module_weights.get(key, 0.0) for key, value in auxiliary.items()) / total_weight
        if total_weight
        else 50.0
    )
    adjustment = max(-10.0, min(10.0, (score - 50.0) * 0.4))
    return round(score, 2), round(adjustment, 2)


def exposure_for_score(state: str, final_score: float, analysis: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    ranges = {"強多": [80, 120], "轉多": [60, 80], "盤整": [40, 60], "轉空": [20, 40], "強空": [0, 20]}
    technical_range = list(ranges.get(state, [40, 60]))
    closes = [float(row["taiex_close"]) for row in history if _number(row.get("taiex_close")) is not None]
    returns = [right / left - 1.0 for left, right in zip(closes[-21:-1], closes[-20:]) if left]
    annualized_volatility = None
    volatility_cap = 150.0
    if len(returns) >= 10:
        average = mean(returns, 0.0)
        variance = mean([(value - average) ** 2 for value in returns], 0.0)
        annualized_volatility = math.sqrt(variance) * math.sqrt(252.0)
        if annualized_volatility > 0:
            volatility_cap = max(20.0, min(150.0, 12.0 / (annualized_volatility * 100.0) * 100.0))
    risk_cap = volatility_cap
    restrictions: list[str] = []
    if analysis.get("synchrony") != "同向確認":
        risk_cap = min(risk_cap, 100.0)
        restrictions.append("加權與櫃買尚未同向")
    if analysis.get("coverage", 0.0) < 0.8:
        risk_cap = min(risk_cap, 100.0)
        restrictions.append("技術資料覆蓋不足80%")
    if final_score < 75:
        risk_cap = min(risk_cap, 100.0)
    adjusted = [min(float(value), risk_cap) for value in technical_range]
    if adjusted[0] > adjusted[1]:
        adjusted[0] = adjusted[1]
    center = round(sum(adjusted) / 2.0)
    return {
        "technical_range": technical_range,
        "risk_adjusted_range": [round(adjusted[0]), round(adjusted[1])],
        "center": center,
        "cash_reserve": max(0, 100 - min(center, 100)),
        "gross_exposure": center,
        "leverage_multiple": round(max(1.0, center / 100.0), 2),
        "annualized_volatility_20d": round(annualized_volatility, 4) if annualized_volatility is not None else None,
        "volatility_cap": round(volatility_cap),
        "restrictions": restrictions,
    }
