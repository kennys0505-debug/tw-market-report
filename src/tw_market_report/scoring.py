from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .stats import mean, pearson, percentile_rank, robust_zscore


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    module: str
    direction: int = 1
    weight: float = 1.0
    label: str = ""


FEATURES: dict[str, FeatureSpec] = {
    "taiex_daily_return_proxy": FeatureSpec("trend_breadth", 1, 1.2, "加權指數單日趨勢（免費官方代理）"),
    "otc_daily_return_proxy": FeatureSpec("trend_breadth", 1, 1.0, "櫃買指數單日趨勢（免費官方代理）"),
    "advance_decline_ratio": FeatureSpec("trend_breadth", 1, 1.0, "上市漲跌家數廣度"),
    "tpex_breadth_proxy": FeatureSpec("trend_breadth", 1, 1.0, "上櫃漲跌家數廣度（免費官方代理）"),
    "volume_price_confirmation": FeatureSpec("trend_breadth", 1, 0.8, "單日量價確認（免費官方代理）"),
    "limit_breadth": FeatureSpec("trend_breadth", 1, 1.0, "漲跌停淨廣度"),
    "limit_strength_proxy": FeatureSpec("trend_breadth", 1, 0.8, "漲跌停強弱（免費官方代理）"),
    "foreign_flow_ratio": FeatureSpec("capital_flow", 1, 1.2, "外資買賣超占成交值"),
    "trust_flow_ratio": FeatureSpec("capital_flow", 1, 1.0, "投信買賣超占成交值"),
    "dealer_flow_ratio": FeatureSpec("capital_flow", 1, 0.6, "自營商買賣超占成交值"),
    "rotation_score": FeatureSpec("capital_flow", 1, 1.0, "資金輪動擴散"),
    "margin_daily_change_proxy": FeatureSpec("leverage_lending", -1, 1.0, "融資餘額日變化（免費官方代理）"),
    "margin_price_divergence_proxy": FeatureSpec("leverage_lending", -1, 1.2, "融資與指數單日背離（免費官方代理）"),
    "borrowed_balance_daily_change_proxy": FeatureSpec("leverage_lending", -1, 1.0, "借券餘額日變化（非借券賣出）"),
    "margin_turnover_pressure_proxy": FeatureSpec("leverage_lending", -1, 1.0, "融資金額／成交值壓力（免費官方代理）"),
    "futures_basis_pct": FeatureSpec("futures", 1, 1.0, "台指期正逆價差"),
    "foreign_futures_net_ratio": FeatureSpec("futures", 1, 1.2, "外資期貨淨部位"),
    "put_call_sentiment": FeatureSpec("options_volatility", 1, 1.0, "Put/Call情緒"),
    "taiwan_vix_level_proxy": FeatureSpec("options_volatility", -1, 1.2, "TAIWAN VIX水準（免費官方代理）"),
    "option_pressure_balance": FeatureSpec("options_volatility", 1, 0.8, "選擇權壓力平衡"),
    "sox_return_5d": FeatureSpec("overseas", 1, 1.2, "費半五日變化"),
    "sox_relative_nasdaq": FeatureSpec("overseas", 1, 0.8, "費半相對Nasdaq"),
    "tsm_adr_premium": FeatureSpec("overseas", 1, 1.0, "台積電ADR溢價"),
    "us_vix_change_5d": FeatureSpec("overseas", -1, 1.0, "美國VIX五日變化"),
    "usd_twd_change_5d": FeatureSpec("overseas", -1, 0.6, "美元兌台幣五日變化"),
    "market_pe_percentile": FeatureSpec("valuation", -1, 1.0, "市場本益比分位"),
    "market_pb_percentile": FeatureSpec("valuation", -1, 0.8, "市場淨值比分位"),
    "dividend_yield_percentile": FeatureSpec("valuation", 1, 0.8, "殖利率分位"),
    "drawdown_52w": FeatureSpec("valuation", 1, 0.6, "距52週高點"),
}


def _feature_score(name: str, value: float, history: list[dict[str, Any]]) -> float | None:
    spec = FEATURES[name]
    series = [row.get("features", {}).get(name) for row in history]
    percentile = percentile_rank(series[-1260:], value)
    zscore = robust_zscore(series[-1260:], value)
    if percentile is None and zscore is None:
        return 50.0
    percentile_score = percentile if percentile is not None else 50.0
    z_score = 50.0 + (zscore or 0.0) * (50.0 / 3.0)
    score = 0.7 * percentile_score + 0.3 * z_score
    return score if spec.direction > 0 else 100.0 - score


def _correlation_groups(names: list[str], history: list[dict[str, Any]], window: int, threshold: float) -> list[list[str]]:
    groups: list[list[str]] = []
    for name in names:
        placed = False
        for group in groups:
            anchor = group[0]
            xs = [row.get("features", {}).get(name) for row in history[-window:]]
            ys = [row.get("features", {}).get(anchor) for row in history[-window:]]
            correlation = pearson(xs, ys)
            if correlation is not None and abs(correlation) > threshold:
                group.append(name)
                placed = True
                break
        if not placed:
            groups.append([name])
    return groups


def score_modules(
    current_features: dict[str, Any],
    history: list[dict[str, Any]],
    correlation_window: int = 252,
    correlation_threshold: float = 0.75,
) -> tuple[dict[str, float], dict[str, float], list[tuple[str, float]], list[tuple[str, float]]]:
    modules = sorted({spec.module for spec in FEATURES.values()})
    scores: dict[str, float] = {}
    coverage: dict[str, float] = {}
    contributions: list[tuple[str, float]] = []
    for module in modules:
        expected = [name for name, spec in FEATURES.items() if spec.module == module]
        available = [name for name in expected if isinstance(current_features.get(name), (int, float))]
        coverage[module] = len(available) / len(expected) if expected else 0.0
        groups = _correlation_groups(available, history, correlation_window, correlation_threshold)
        group_scores: list[float] = []
        for group in groups:
            weighted = []
            weights = []
            for name in group:
                raw_score = _feature_score(name, float(current_features[name]), history)
                if raw_score is None:
                    continue
                weighted.append(raw_score * FEATURES[name].weight)
                weights.append(FEATURES[name].weight)
                contributions.append((FEATURES[name].label, raw_score - 50.0))
            if weights:
                group_scores.append(sum(weighted) / sum(weights))
        # A missing indicator must not disappear from the module average.  Treat
        # it as neutral until it becomes observable, so one available feature
        # cannot dominate a low-readiness module.  ``coverage`` above remains
        # the observed-data ratio used by the confidence calculation.
        group_scores.extend(50.0 for name in expected if name not in available)
        scores[module] = mean(group_scores, 50.0)
    positive = sorted((item for item in contributions if item[1] > 0), key=lambda item: item[1], reverse=True)[:5]
    negative = sorted((item for item in contributions if item[1] < 0), key=lambda item: item[1])[:5]
    return scores, coverage, positive, negative


STATE_ORDER = {"強空": 0, "轉空": 1, "盤整": 2, "轉多": 3, "強多": 4}


def classify_state(
    score: float,
    module_scores: dict[str, float],
    history: list[dict[str, Any]],
    features: dict[str, Any] | None = None,
) -> str:
    features = features or {}
    positive = sum(value >= 55 for value in module_scores.values())
    negative = sum(value <= 45 for value in module_scores.values())
    previous_score = history[-1].get("composite_score") if history else None
    bullish_confirmation = bool(
        features.get("price_reversal")
        or features.get("breadth_reversal")
        or features.get("limit_contraction")
        or float(features.get("limit_breadth") or 0) > 0
    )
    bearish_confirmation = bool(
        float(features.get("taiex_ma20_gap") or 0) < 0
        or float(features.get("advance_decline_ratio") or 0) < 0
        or float(features.get("limit_breadth") or 0) < 0
    )
    if score >= 65 and positive >= 5:
        candidate = "強多"
    elif score >= 60 and previous_score is not None and float(previous_score) >= 60 and positive >= 4 and bullish_confirmation:
        candidate = "轉多"
    elif score <= 30 and negative >= 4:
        candidate = "強空"
    elif score <= 40 and previous_score is not None and float(previous_score) <= 40 and negative >= 4 and bearish_confirmation:
        candidate = "轉空"
    else:
        candidate = "盤整"
    if not history:
        return candidate
    previous = str(history[-1].get("domestic_market_state", "盤整"))
    recent = [str(row.get("domestic_market_state", "盤整")) for row in history[-3:]]
    if previous != candidate and len(set(recent)) > 1 and abs(STATE_ORDER[candidate] - STATE_ORDER[previous]) < 2:
        return previous
    return candidate


def reversal_stage(features: dict[str, Any], state: str, history: list[dict[str, Any]]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    extreme_count = 0
    down_pct = features.get("limit_down_percentile_5y")
    if isinstance(down_pct, (int, float)) and down_pct >= 95:
        extreme_count += 1
        reasons.append("跌停比例進入五年極端區")
    margin_pct = features.get("margin_stress_percentile")
    if isinstance(margin_pct, (int, float)) and margin_pct <= 10:
        extreme_count += 1
        reasons.append("融資壓力代理接近歷史低檔")
    vix_pct = features.get("taiwan_vix_percentile")
    if isinstance(vix_pct, (int, float)) and vix_pct >= 95:
        extreme_count += 1
        reasons.append("TAIWAN VIX進入極端區")
    valuation_pct = features.get("valuation_stress_percentile")
    if isinstance(valuation_pct, (int, float)) and valuation_pct >= 95:
        extreme_count += 1
        reasons.append("估值／回撤壓力進入極端區")
    confirmations = sum(
        bool(features.get(key))
        for key in ("price_reversal", "breadth_reversal", "limit_contraction", "institution_reversal", "futures_reversal", "options_reversal")
    )
    if confirmations >= 3 and history and history[-1].get("reversal_stage") in {"初步反轉", "反轉確認"}:
        return "反轉確認", reasons
    if confirmations >= 2:
        return "初步反轉", reasons
    if extreme_count >= 3:
        return "極端觀察", reasons
    if state in {"強多", "強空"}:
        return "趨勢延續", reasons
    return "無", reasons


def apply_overnight_overlay(features: dict[str, Any]) -> tuple[str, int]:
    risk = 0
    if float(features.get("us_vix_change_5d") or 0) > 0.15:
        risk += 1
    if float(features.get("sox_return_5d") or 0) < -0.04:
        risk += 1
    if float(features.get("tsm_adr_premium") or 0) < -0.02:
        risk += 1
    if float(features.get("usd_twd_change_5d") or 0) > 0.015:
        risk += 1
    positive = int(float(features.get("sox_return_5d") or 0) > 0.04) + int(float(features.get("tsm_adr_premium") or 0) > 0.02)
    if risk >= 3:
        return "高風險", -10
    if risk >= 2:
        return "偏空", -5
    if positive >= 2 and risk == 0:
        return "偏多", 5
    return "中性", 0

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .stats import mean, pearson, percentile_rank, robust_zscore


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    module: str
    direction: int = 1
    weight: float = 1.0
    label: str = ""


FEATURES: dict[str, FeatureSpec] = {
    "taiex_ma20_gap": FeatureSpec("trend_breadth", 1, 1.2, "加權指數相對20日線"),
    "taiex_ma60_gap": FeatureSpec("trend_breadth", 1, 1.2, "加權指數相對60日線"),
    "otc_ma20_gap": FeatureSpec("trend_breadth", 1, 1.0, "櫃買指數相對20日線"),
    "advance_decline_ratio": FeatureSpec("trend_breadth", 1, 1.0, "上漲家數廣度"),
    "above_ma20_ratio": FeatureSpec("trend_breadth", 1, 1.0, "站上20日線家數"),
    "volume_price_confirmation": FeatureSpec("trend_breadth", 1, 0.8, "量價確認"),
    "limit_breadth": FeatureSpec("trend_breadth", 1, 1.0, "漲跌停淨廣度"),
    "foreign_flow_ratio": FeatureSpec("capital_flow", 1, 1.2, "外資買賣超占成交值"),
    "trust_flow_ratio": FeatureSpec("capital_flow", 1, 1.0, "投信買賣超占成交值"),
    "dealer_flow_ratio": FeatureSpec("capital_flow", 1, 0.6, "自營商買賣超占成交值"),
    "rotation_score": FeatureSpec("capital_flow", 1, 1.0, "資金輪動擴散"),
    "margin_20d_change": FeatureSpec("leverage_lending", -1, 1.0, "20日融資增幅"),
    "margin_price_divergence": FeatureSpec("leverage_lending", -1, 1.2, "融資與指數背離"),
    "borrowed_sell_5d_change": FeatureSpec("leverage_lending", -1, 1.0, "借券賣出餘額變化"),
    "margin_stress_proxy": FeatureSpec("leverage_lending", 1, 1.0, "融資壓力代理"),
    "futures_basis_pct": FeatureSpec("futures", 1, 1.0, "台指期正逆價差"),
    "foreign_futures_net_ratio": FeatureSpec("futures", 1, 1.2, "外資期貨淨部位"),
    "noninst_short_long_ratio": FeatureSpec("futures", -1, 0.8, "非三大法人空多比"),
    "put_call_sentiment": FeatureSpec("options_volatility", 1, 1.0, "Put/Call情緒"),
    "taiwan_vix_change_5d": FeatureSpec("options_volatility", -1, 1.2, "TAIWAN VIX五日變化"),
    "option_pressure_balance": FeatureSpec("options_volatility", 1, 0.8, "選擇權壓力平衡"),
    "sox_return_5d": FeatureSpec("overseas", 1, 1.2, "費半五日變化"),
    "sox_relative_nasdaq": FeatureSpec("overseas", 1, 0.8, "費半相對Nasdaq"),
    "tsm_adr_premium": FeatureSpec("overseas", 1, 1.0, "台積電ADR溢價"),
    "us_vix_change_5d": FeatureSpec("overseas", -1, 1.0, "美國VIX五日變化"),
    "usd_twd_change_5d": FeatureSpec("overseas", -1, 0.6, "美元兌台幣五日變化"),
    "market_pe_percentile": FeatureSpec("valuation", -1, 1.0, "市場本益比分位"),
    "market_pb_percentile": FeatureSpec("valuation", -1, 0.8, "市場淨值比分位"),
    "dividend_yield_percentile": FeatureSpec("valuation", 1, 0.8, "殖利率分位"),
    "drawdown_52w": FeatureSpec("valuation", 1, 0.6, "距52週高點"),
}


def _feature_score(name: str, value: float, history: list[dict[str, Any]]) -> float | None:
    spec = FEATURES[name]
    series = [row.get("features", {}).get(name) for row in history]
    percentile = percentile_rank(series[-1260:], value)
    zscore = robust_zscore(series[-1260:], value)
    if percentile is None and zscore is None:
        return 50.0
    percentile_score = percentile if percentile is not None else 50.0
    z_score = 50.0 + (zscore or 0.0) * (50.0 / 3.0)
    score = 0.7 * percentile_score + 0.3 * z_score
    return score if spec.direction > 0 else 100.0 - score


def _correlation_groups(names: list[str], history: list[dict[str, Any]], window: int, threshold: float) -> list[list[str]]:
    groups: list[list[str]] = []
    for name in names:
        placed = False
        for group in groups:
            anchor = group[0]
            xs = [row.get("features", {}).get(name) for row in history[-window:]]
            ys = [row.get("features", {}).get(anchor) for row in history[-window:]]
            correlation = pearson(xs, ys)
            if correlation is not None and abs(correlation) > threshold:
                group.append(name)
                placed = True
                break
        if not placed:
            groups.append([name])
    return groups


def score_modules(
    current_features: dict[str, Any],
    history: list[dict[str, Any]],
    correlation_window: int = 252,
    correlation_threshold: float = 0.75,
) -> tuple[dict[str, float], dict[str, float], list[tuple[str, float]], list[tuple[str, float]]]:
    modules = sorted({spec.module for spec in FEATURES.values()})
    scores: dict[str, float] = {}
    coverage: dict[str, float] = {}
    contributions: list[tuple[str, float]] = []
    for module in modules:
        expected = [name for name, spec in FEATURES.items() if spec.module == module]
        available = [name for name in expected if isinstance(current_features.get(name), (int, float))]
        coverage[module] = len(available) / len(expected) if expected else 0.0
        groups = _correlation_groups(available, history, correlation_window, correlation_threshold)
        group_scores: list[float] = []
        for group in groups:
            weighted = []
            weights = []
            for name in group:
                raw_score = _feature_score(name, float(current_features[name]), history)
                if raw_score is None:
                    continue
                weighted.append(raw_score * FEATURES[name].weight)
                weights.append(FEATURES[name].weight)
                contributions.append((FEATURES[name].label, raw_score - 50.0))
            if weights:
                group_scores.append(sum(weighted) / sum(weights))
        # A missing indicator must not disappear from the module average.  Treat
        # it as neutral until it becomes observable, so one available feature
        # cannot dominate a low-readiness module.  ``coverage`` above remains
        # the observed-data ratio used by the confidence calculation.
        group_scores.extend(50.0 for name in expected if name not in available)
        scores[module] = mean(group_scores, 50.0)
    positive = sorted((item for item in contributions if item[1] > 0), key=lambda item: item[1], reverse=True)[:5]
    negative = sorted((item for item in contributions if item[1] < 0), key=lambda item: item[1])[:5]
    return scores, coverage, positive, negative


STATE_ORDER = {"強空": 0, "轉空": 1, "盤整": 2, "轉多": 3, "強多": 4}


def classify_state(
    score: float,
    module_scores: dict[str, float],
    history: list[dict[str, Any]],
    features: dict[str, Any] | None = None,
) -> str:
    features = features or {}
    positive = sum(value >= 55 for value in module_scores.values())
    negative = sum(value <= 45 for value in module_scores.values())
    previous_score = history[-1].get("composite_score") if history else None
    bullish_confirmation = bool(
        features.get("price_reversal")
        or features.get("breadth_reversal")
        or features.get("limit_contraction")
        or float(features.get("limit_breadth") or 0) > 0
    )
    bearish_confirmation = bool(
        float(features.get("taiex_ma20_gap") or 0) < 0
        or float(features.get("advance_decline_ratio") or 0) < 0
        or float(features.get("limit_breadth") or 0) < 0
    )
    if score >= 65 and positive >= 5:
        candidate = "強多"
    elif score >= 60 and previous_score is not None and float(previous_score) >= 60 and positive >= 4 and bullish_confirmation:
        candidate = "轉多"
    elif score <= 30 and negative >= 4:
        candidate = "強空"
    elif score <= 40 and previous_score is not None and float(previous_score) <= 40 and negative >= 4 and bearish_confirmation:
        candidate = "轉空"
    else:
        candidate = "盤整"
    if not history:
        return candidate
    previous = str(history[-1].get("domestic_market_state", "盤整"))
    recent = [str(row.get("domestic_market_state", "盤整")) for row in history[-3:]]
    if previous != candidate and len(set(recent)) > 1 and abs(STATE_ORDER[candidate] - STATE_ORDER[previous]) < 2:
        return previous
    return candidate


def reversal_stage(features: dict[str, Any], state: str, history: list[dict[str, Any]]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    extreme_count = 0
    down_pct = features.get("limit_down_percentile_5y")
    if isinstance(down_pct, (int, float)) and down_pct >= 95:
        extreme_count += 1
        reasons.append("跌停比例進入五年極端區")
    margin_pct = features.get("margin_stress_percentile")
    if isinstance(margin_pct, (int, float)) and margin_pct <= 10:
        extreme_count += 1
        reasons.append("融資壓力代理接近歷史低檔")
    vix_pct = features.get("taiwan_vix_percentile")
    if isinstance(vix_pct, (int, float)) and vix_pct >= 95:
        extreme_count += 1
        reasons.append("TAIWAN VIX進入極端區")
    valuation_pct = features.get("valuation_stress_percentile")
    if isinstance(valuation_pct, (int, float)) and valuation_pct >= 95:
        extreme_count += 1
        reasons.append("估值／回撤壓力進入極端區")
    confirmations = sum(
        bool(features.get(key))
        for key in ("price_reversal", "breadth_reversal", "limit_contraction", "institution_reversal", "futures_reversal", "options_reversal")
    )
    if confirmations >= 3 and history and history[-1].get("reversal_stage") in {"初步反轉", "反轉確認"}:
        return "反轉確認", reasons
    if confirmations >= 2:
        return "初步反轉", reasons
    if extreme_count >= 3:
        return "極端觀察", reasons
    if state in {"強多", "強空"}:
        return "趨勢延續", reasons
    return "無", reasons


def apply_overnight_overlay(features: dict[str, Any]) -> tuple[str, int]:
    risk = 0
    if float(features.get("us_vix_change_5d") or 0) > 0.15:
        risk += 1
    if float(features.get("sox_return_5d") or 0) < -0.04:
        risk += 1
    if float(features.get("tsm_adr_premium") or 0) < -0.02:
        risk += 1
    if float(features.get("usd_twd_change_5d") or 0) > 0.015:
        risk += 1
    positive = int(float(features.get("sox_return_5d") or 0) > 0.04) + int(float(features.get("tsm_adr_premium") or 0) > 0.02)
    if risk >= 3:
        return "高風險", -10
    if risk >= 2:
        return "偏空", -5
    if positive >= 2 and risk == 0:
        return "偏多", 5
    return "中性", 0
