from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import ReportConfig
from .fixture import fixture_current, fixture_history
from .history import load_history, load_json, upsert_history, write_json
from .limits import attach_limit_percentiles, combine_limit_stats, historical_analogs
from .models import LimitStats, MarketSnapshot, SourceStatus
from .scoring import FEATURES, apply_overnight_overlay, classify_state, module_calculation_notes, reversal_stage, score_modules
from .sources.derivatives import DerivativesCollector
from .sources.domestic import DomesticCollector
from .sources.overseas import OverseasCollector
from .stats import mean, percentile_rank, robust_zscore, safe_div
from .technical import auxiliary_adjustment, exposure_for_score, technical_analysis

PROXY_DEFINITIONS = {
    "taiex_daily_return_proxy": "取代加權指數20／60日均線；使用官方收盤價相對前一有效交易日報酬。",
    "otc_daily_return_proxy": "取代櫃買20日均線；使用櫃買官方指數相對前一有效交易日報酬。",
    "tpex_breadth_proxy": "取代逐股站上20日線比例；使用上櫃官方漲跌家數淨廣度。",
    "limit_strength_proxy": "以官方漲停／跌停家數強弱比取對數，保留方向且壓縮極端值。",
    "margin_daily_change_proxy": "取代20日融資變化；使用官方融資金額相對前一有效交易日變化。",
    "margin_price_divergence_proxy": "融資日變化減加權指數單日報酬；不是20日背離。",
    "borrowed_balance_daily_change_proxy": "使用官方借券餘額日變化；不是借券賣出餘額。",
    "margin_turnover_pressure_proxy": "官方融資金額（仟元換算元）除以當日市場成交值；不是整戶維持率。",
    "taiwan_vix_level_proxy": "取代五日變化；使用當日TAIWAN VIX水準。",
    "rotation_score": "資金輪動代理＝1－台積電成交值／上市市場成交值；數值越高代表成交越不集中於台積電，不代表實際資金申購或贖回。",
    "foreign_futures_scheme7_score": "外資期貨方案七＝部位歷史水位40%＋5日變化30%＋占全市場OI 20%＋方向持續性10%。",
}


def _sma(values: list[float], days: int) -> float | None:
    return mean(values[-days:]) if len(values) >= days else None


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return current / previous - 1.0


def _distribution_score(values: list[float], value: float) -> float:
    """Convert a raw observation to the report's five-year 0-100 scale."""
    clean = [float(item) for item in values if isinstance(item, (int, float))]
    if len(clean) < 20:
        return 50.0
    window = clean[-1260:]
    percentile = percentile_rank(window, value)
    zscore = robust_zscore(window, value)
    percentile_score = percentile if percentile is not None else 50.0
    z_score = 50.0 + (zscore or 0.0) * (50.0 / 3.0)
    return max(0.0, min(100.0, 0.7 * percentile_score + 0.3 * z_score))


def _foreign_futures_scheme7(current: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, float]:
    """Build the approved level/change/share/persistence foreign-futures score."""
    net = current.get("foreign_futures_net")
    if not isinstance(net, (int, float)):
        return {}
    observations = [
        (row.get("features", {}).get("foreign_futures_net"), row.get("features", {}).get("futures_market_oi"))
        for row in history
    ]
    nets = [float(value) for value, _ in observations if isinstance(value, (int, float))]
    level_score = _distribution_score(nets, float(net))

    market_oi = current.get("futures_market_oi")
    market_share = None
    share_score = 50.0
    if isinstance(market_oi, (int, float)) and float(market_oi) > 0:
        market_share = float(net) / float(market_oi)
        share_history = [
            float(value) / float(oi)
            for value, oi in observations
            if isinstance(value, (int, float)) and isinstance(oi, (int, float)) and float(oi) > 0
        ]
        share_score = _distribution_score(share_history, market_share)

    change_5d = None
    change_5d_share = None
    change_score = 50.0
    if len(nets) >= 5:
        change_5d = float(net) - nets[-5]
        if isinstance(market_oi, (int, float)) and float(market_oi) > 0:
            change_5d_share = change_5d / float(market_oi)
            valid = [
                (float(value), float(oi))
                for value, oi in observations
                if isinstance(value, (int, float)) and isinstance(oi, (int, float)) and float(oi) > 0
            ]
            historical_changes = [
                (valid[index][0] - valid[index - 5][0]) / valid[index][1]
                for index in range(5, len(valid))
            ]
            change_score = _distribution_score(historical_changes, change_5d_share)

    persistence = 0.0
    recent = (nets + [float(net)])[-6:]
    if len(recent) >= 2:
        directions = [
            1.0 if right > left else -1.0 if right < left else 0.0
            for left, right in zip(recent, recent[1:])
        ]
        persistence = mean(directions, 0.0)
    persistence_score = 50.0 + 50.0 * persistence
    result = {
        "foreign_futures_level_score": level_score,
        "foreign_futures_change_score": change_score,
        "foreign_futures_share_score": share_score,
        "foreign_futures_persistence_score": persistence_score,
        "foreign_futures_persistence": persistence,
    }
    if market_share is not None:
        result["foreign_futures_market_share"] = market_share
    if change_5d is not None:
        result["foreign_futures_change_5d"] = change_5d
    if change_5d_share is not None:
        result["foreign_futures_change_5d_share"] = change_5d_share
    if market_share is not None and change_5d_share is not None:
        scheme_score = 0.4 * level_score + 0.3 * change_score + 0.2 * share_score + 0.1 * persistence_score
        result["foreign_futures_scheme7_score"] = max(0.0, min(100.0, scheme_score))
    return result


def _rotation_score(market_turnover: float | None, tsmc_turnover: float | None) -> float | None:
    """Return an unscaled TSMC concentration complement for historical ranking.

    The scoring layer already converts this value to a rolling percentile and
    robust z-score.  A fixed multiplier would not add information and could
    collapse all observations above an arbitrary concentration threshold.
    """
    if market_turnover is None or tsmc_turnover is None or market_turnover <= 0 or tsmc_turnover < 0:
        return None
    concentration = safe_div(float(tsmc_turnover), float(market_turnover))
    return max(0.0, min(1.0, 1.0 - concentration))


def _sanitize_history_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep usable domestic history while discarding implausible derivatives data."""
    cleaned = dict(row)
    features = dict(row.get("features", {}))
    # Rows produced by the old BFI82U parser mistook missing columns for zero
    # and could let the foreign-dealer row overwrite foreign institutions.
    # Do not use those values as calibration history for the corrected parser.
    if features.get("institutional_flow_parser_version") != "bfi82u_v2":
        for key in ("foreign_flow_ratio", "trust_flow_ratio", "dealer_flow_ratio"):
            features.pop(key, None)
    rotation = _rotation_score(features.get("market_turnover"), features.get("tsmc_turnover"))
    if rotation is not None:
        features["rotation_score"] = rotation
    spot = row.get("taiex_close")
    futures_price = features.get("tx_settlement")
    try:
        plausible = spot is not None and futures_price is not None and 0.7 <= float(futures_price) / float(spot) <= 1.3
    except (TypeError, ValueError, ZeroDivisionError):
        plausible = False
    if futures_price is not None and not plausible:
        for key in ("tx_settlement", "futures_basis", "futures_basis_pct", "annualized_basis"):
            features.pop(key, None)
    cleaned["features"] = features
    return cleaned


class ReportPipeline:
    def __init__(self, config: ReportConfig) -> None:
        self.config = config
        self.root = config.root
        self.history_path = self.root / "data" / "history.jsonl"
        self.docs = self.root / "docs"

    def run(self, mode: str, trade_date: date | None = None, fixture: bool = False) -> MarketSnapshot:
        now = datetime.now(ZoneInfo(self.config.raw.get("timezone", "Asia/Taipei")))
        trade_date = trade_date or now.date()
        if fixture:
            history = fixture_history()
        else:
            # Features for a report may only use completed, strictly earlier
            # trading days.  This prevents failed same-day rows and accidental
            # future-dated rows from leaking into moving averages or reversals.
            cutoff = trade_date.isoformat()
            history = [
                _sanitize_history_row(row) for row in load_history(self.history_path)
                if row.get("report_mode") == "close"
                and str(row.get("trade_date", "")) < cutoff
                and row.get("taiex_close") is not None
            ]
        if mode == "premarket" and not fixture:
            return self._premarket(trade_date, now, history)
        return self._close(trade_date, now, history, fixture)

    def _close(self, trade_date: date, now: datetime, history: list[dict], fixture: bool) -> MarketSnapshot:
        if fixture:
            features, limits, statuses, zones = fixture_current()
        else:
            features, limits, statuses = DomesticCollector(self.config.sources).collect(trade_date)
            derivatives, zones, derivative_statuses = DerivativesCollector(self.config.sources).collect(
                trade_date, features.get("taiex_close")
            )
            features.update(derivatives)
            statuses.extend(derivative_statuses)
            overseas, overseas_statuses = OverseasCollector(self.config.sources).collect(features.get("tsmc_close"))
            features.update(overseas)
            statuses.extend(overseas_statuses)
        if "twse" not in limits:
            limits["twse"] = LimitStats("twse")
        if "tpex" not in limits:
            limits["tpex"] = LimitStats("tpex")
        limits["combined"] = combine_limit_stats([limits["twse"], limits["tpex"]])
        features["_tpex_limit_breadth"] = limits["tpex"].limit_breadth
        for limit_stats in limits.values():
            attach_limit_percentiles(limit_stats, history)
        features.update(self._engineer_features(features, limits["combined"], history, zones))
        features["active_feature_mode"] = "free_official_proxy_v2"
        features["feature_proxy_definitions"] = PROXY_DEFINITIONS
        analogs: list[dict[str, Any]] = []
        post_returns: dict[str, Any] = {}
        for market, limit_stats in limits.items():
            market_analogs, market_returns = historical_analogs(limit_stats, history)
            analogs.extend(market_analogs)
            post_returns[market] = market_returns
        gate = load_json(self.root / "data" / "backtest.json", {}) or {}
        limit_scoring_enabled = bool(fixture or (limits["combined"].universe_verified and gate.get("limit_scoring_enabled", False)))
        features["limit_scoring_enabled"] = limit_scoring_enabled
        scoring_features = dict(features)
        if not limit_scoring_enabled:
            scoring_features.pop("limit_breadth", None)
        module_scores, _, positives, negatives = score_modules(
            scoring_features,
            history,
            int(self.config.raw.get("correlation_window", 252)),
            float(self.config.raw.get("correlation_threshold", 0.75)),
        )
        calculation_notes = module_calculation_notes(
            scoring_features,
            history,
            module_scores,
            int(self.config.raw.get("correlation_window", 252)),
            float(self.config.raw.get("correlation_threshold", 0.75)),
        )
        # Observed coverage describes source data, not whether a validated
        # feature is currently allowed to affect the score.  For example,
        # limit breadth remains observed even while its backtest gate is closed.
        observed_coverage = {}
        history_coverage = {}
        for module in module_scores:
            expected = [name for name, spec in FEATURES.items() if spec.module == module]
            available = [name for name in expected if isinstance(features.get(name), (int, float))]
            observed_coverage[module] = len(available) / len(expected) if expected else 0.0
            maturity = [
                min(
                    sum(isinstance(row.get("features", {}).get(name), (int, float)) for row in history) / 20.0,
                    1.0,
                )
                for name in expected
            ]
            history_coverage[module] = mean(maturity, 0.0)
        if features.get("overseas_data_stale"):
            module_scores["overseas"] = 50.0
            observed_coverage["overseas"] = 0.0
        # Missing observations remain neutral in scoring, but the displayed
        # coverage must describe measured data rather than imputation.
        coverage = dict(observed_coverage)
        imputed = sorted(
            name for name in FEATURES
            if not isinstance(features.get(name), (int, float))
        )
        technical = technical_analysis(features, history)
        auxiliary, adjustment = auxiliary_adjustment(module_scores, self.config.module_weights)
        composite = max(0.0, min(100.0, float(technical["score"]) + adjustment))
        core_ready = any(status.name == "TWSE收盤行情" and status.status in {"ready", "fixture"} for status in statuses)
        features["core_data_ready"] = core_ready
        if not core_ready:
            state = history[-1].get("domestic_market_state", "盤整") if history else "盤整"
            composite = float(history[-1].get("composite_score", 50.0)) if history else 50.0
        else:
            state = str(technical["state"])
        reversal, reasons = reversal_stage(features, state, history)
        if core_ready:
            reversal = str(technical["stage"])
            reasons = list(technical.get("taiex", {}).get("reasons", []))[:2] + list(technical.get("otc", {}).get("reasons", []))[:2]
        weighted_coverage = sum(observed_coverage.get(name, 0.0) * weight for name, weight in self.config.module_weights.items())
        alignment = max(sum(score >= 55 for score in module_scores.values()), sum(score <= 45 for score in module_scores.values()))
        technical_coverage = float(technical.get("coverage", 0.0))
        confidence = (
            "高"
            if weighted_coverage >= 0.75 and technical_coverage >= 0.85 and technical.get("synchrony") == "同向確認"
            else "中"
            if weighted_coverage >= 0.60 and technical_coverage >= 0.65
            else "低"
        )
        exposure = exposure_for_score(str(state), composite, technical, history + ([{"taiex_close": features.get("taiex_close")}] if core_ready else []))
        snapshot = MarketSnapshot(
            trade_date=trade_date.isoformat(),
            report_mode="close",
            generated_at=now.isoformat(),
            domestic_market_state=str(state),
            composite_score=round(composite, 2),
            technical_score=float(technical["score"]),
            technical_state=str(technical["state"]),
            technical_analysis=technical,
            auxiliary_score=auxiliary,
            auxiliary_adjustment=adjustment,
            exposure_details=exposure,
            confidence=confidence,
            model_exposure_range=list(exposure["risk_adjusted_range"]),
            shadow_mode=self.config.shadow_mode,
            module_scores={key: round(value, 2) for key, value in module_scores.items()},
            module_coverage={key: round(value, 3) for key, value in coverage.items()},
            module_observed_coverage={key: round(value, 3) for key, value in observed_coverage.items()},
            module_history_coverage={key: round(value, 3) for key, value in history_coverage.items()},
            module_calculation_notes=calculation_notes,
            imputed_score_features=imputed,
            features=features,
            limits=limits,
            historical_limit_analogs=analogs,
            post_analog_returns=post_returns,
            options_pressure_zones=zones,
            positive_drivers=[f"{label}（+{delta:.1f}）" for label, delta in positives[:3]],
            negative_drivers=[f"{label}（{delta:.1f}）" for label, delta in negatives[:3]],
            reversal_stage=reversal,
            reversal_reasons=reasons,
            data_freshness={status.name: status.as_of or "未知" for status in statuses},
            source_status=statuses,
            history=(history + ([{"trade_date": trade_date.isoformat(), "composite_score": composite, "taiex_close": features.get("taiex_close")}] if core_ready else []))[-60:],
        )
        return snapshot

    def _premarket(self, trade_date: date, now: datetime, history: list[dict]) -> MarketSnapshot:
        base_data = load_json(self.docs / "close-latest.json")
        if not base_data:
            raise RuntimeError("Premarket report needs docs/close-latest.json from the latest close run")
        tsmc_close = base_data.get("features", {}).get("tsmc_close")
        overseas, statuses = OverseasCollector(self.config.sources).collect(tsmc_close)
        risk_state, adjustment = apply_overnight_overlay(overseas)
        base_range = list(base_data.get("model_exposure_range", [40, 60]))
        adjusted = [max(0, min(150, value + adjustment)) for value in base_range]
        exposure_details = dict(base_data.get("exposure_details", {}))
        exposure_details["risk_adjusted_range"] = adjusted
        exposure_details["center"] = round(sum(adjusted) / 2.0)
        exposure_details["gross_exposure"] = exposure_details["center"]
        exposure_details["cash_reserve"] = max(0, 100 - min(exposure_details["center"], 100))
        exposure_details["leverage_multiple"] = round(max(1.0, exposure_details["center"] / 100.0), 2)
        exposure_details["overnight_adjustment"] = adjustment
        snapshot = MarketSnapshot(
            trade_date=trade_date.isoformat(),
            report_mode="premarket",
            generated_at=now.isoformat(),
            domestic_market_state=base_data.get("domestic_market_state", "盤整"),
            overnight_risk_state=risk_state,
            composite_score=float(base_data.get("composite_score", 50.0)),
            technical_score=float(base_data.get("technical_score", 50.0)),
            technical_state=base_data.get("technical_state", "盤整"),
            technical_analysis=base_data.get("technical_analysis", {}),
            auxiliary_score=float(base_data.get("auxiliary_score", 50.0)),
            auxiliary_adjustment=float(base_data.get("auxiliary_adjustment", 0.0)),
            exposure_details=exposure_details,
            confidence=base_data.get("confidence", "低"),
            model_exposure_range=adjusted,
            shadow_mode=bool(base_data.get("shadow_mode", True)),
            module_scores=base_data.get("module_scores", {}),
            module_coverage=base_data.get("module_coverage", {}),
            module_observed_coverage=base_data.get("module_observed_coverage", base_data.get("module_coverage", {})),
            module_history_coverage=base_data.get("module_history_coverage", {}),
            module_calculation_notes=base_data.get("module_calculation_notes", {}),
            imputed_score_features=base_data.get("imputed_score_features", []),
            features={**base_data.get("features", {}), **overseas, "overnight_exposure_adjustment": adjustment},
            limits={key: LimitStats(**value) for key, value in base_data.get("limits", {}).items()},
            historical_limit_analogs=base_data.get("historical_limit_analogs", []),
            post_analog_returns=base_data.get("post_analog_returns", {}),
            options_pressure_zones=base_data.get("options_pressure_zones", {}),
            positive_drivers=base_data.get("positive_drivers", []),
            negative_drivers=base_data.get("negative_drivers", []),
            reversal_stage=base_data.get("reversal_stage", "無"),
            reversal_reasons=base_data.get("reversal_reasons", []),
            data_freshness={status.name: status.as_of or "未知" for status in statuses},
            source_status=statuses,
            history=base_data.get("history", []),
        )
        return snapshot

    def _engineer_features(self, current: dict, limits: LimitStats, history: list[dict], zones: dict) -> dict[str, Any]:
        result: dict[str, Any] = {
            "limit_breadth": limits.limit_breadth,
            "limit_strength_proxy": math.log(max(limits.strength_ratio, 1e-9)),
            "limit_up_percentile_5y": limits.up_percentile_5y,
            "limit_down_percentile_5y": limits.down_percentile_5y,
            "option_pressure_balance": zones.get("pressure_balance"),
        }
        rotation = _rotation_score(current.get("market_turnover"), current.get("tsmc_turnover"))
        if rotation is not None:
            result["rotation_score"] = rotation
        closes = [float(row["taiex_close"]) for row in history if row.get("taiex_close") is not None]
        current_close = current.get("taiex_close")
        if current_close is not None:
            for days in (20, 60, 120, 240):
                average = _sma(closes + [float(current_close)], days)
                if average:
                    result[f"taiex_ma{days}_gap"] = float(current_close) / average - 1.0
            if history:
                previous_close = history[-1].get("taiex_close")
                market_return = _pct_change(float(current_close), float(previous_close)) if previous_close else None
                if market_return is not None:
                    result["taiex_daily_return_proxy"] = market_return
                turnover_history = [row.get("features", {}).get("market_turnover") for row in history]
                turnover_clean = [float(value) for value in turnover_history if value is not None]
                turnover_average = _sma(turnover_clean + ([float(current["market_turnover"])] if current.get("market_turnover") else []), 20)
                if market_return is not None:
                    volume_ratio = safe_div(float(current.get("market_turnover") or 0), turnover_average, 1.0)
                    result["volume_price_confirmation"] = market_return * volume_ratio
        otc_closes = [row.get("features", {}).get("otc_close") for row in history]
        otc_clean = [float(value) for value in otc_closes if value is not None]
        if current.get("otc_close") is not None:
            average = _sma(otc_clean + [float(current["otc_close"])], 20)
            if average:
                result["otc_ma20_gap"] = float(current["otc_close"]) / average - 1.0
            if otc_clean:
                result["otc_daily_return_proxy"] = _pct_change(float(current["otc_close"]), otc_clean[-1])
        tpex = current.get("_tpex_limit_breadth")
        if isinstance(tpex, (int, float)):
            result["tpex_breadth_proxy"] = float(tpex)
        result.update(_foreign_futures_scheme7(current, history))
        previous_features = history[-1].get("features", {}) if history else {}
        margin_history = [row.get("features", {}).get("margin_balance") for row in history]
        margin_clean = [float(value) for value in margin_history if value is not None]
        if current.get("margin_balance") is not None and margin_clean:
            margin_daily = _pct_change(float(current["margin_balance"]), margin_clean[-1])
            if margin_daily is not None:
                result["margin_daily_change_proxy"] = margin_daily
                if result.get("taiex_daily_return_proxy") is not None:
                    result["margin_price_divergence_proxy"] = margin_daily - float(result["taiex_daily_return_proxy"])
        if current.get("margin_balance") is not None and current.get("market_turnover"):
            result["margin_turnover_pressure_proxy"] = (
                float(current["margin_balance"]) * 1000.0 / float(current["market_turnover"])
            )
        if current.get("margin_balance") is not None and len(margin_clean) >= 20:
            prior = margin_clean[-20]
            result["margin_20d_change"] = _pct_change(float(current["margin_balance"]), prior)
            if current_close is not None and history and history[-1].get("taiex_close"):
                price_change = _pct_change(float(current_close), float(history[-1]["taiex_close"])) or 0.0
                margin_change = _pct_change(float(current["margin_balance"]), margin_clean[-1]) or 0.0
                result["margin_price_divergence"] = max(0.0, margin_change - price_change)
        if current.get("borrowed_sell_balance") is not None:
            borrowed_history = [
                row.get("features", {}).get("borrowed_sell_balance") for row in history
                if row.get("features", {}).get("borrowed_sell_balance") is not None
            ]
            if len(borrowed_history) >= 5:
                result["borrowed_sell_5d_change"] = _pct_change(
                    float(current["borrowed_sell_balance"]), float(borrowed_history[-5])
                )
        borrowed_balance_history = [
            row.get("features", {}).get("borrowed_balance") for row in history
            if row.get("features", {}).get("borrowed_balance") is not None
        ]
        if current.get("borrowed_balance") is not None and borrowed_balance_history:
            result["borrowed_balance_daily_change_proxy"] = _pct_change(
                float(current["borrowed_balance"]), float(borrowed_balance_history[-1])
            )
        if current.get("taiwan_vix") is not None:
            result["taiwan_vix_level_proxy"] = float(current["taiwan_vix"])
            history_vix = [row.get("features", {}).get("taiwan_vix") for row in history]
            history_vix = [float(value) for value in history_vix if value is not None]
            if len(history_vix) >= 5:
                result["taiwan_vix_change_5d"] = _pct_change(float(current["taiwan_vix"]), history_vix[-5])
            result["taiwan_vix_percentile"] = percentile_rank(history_vix[-1260:], float(current["taiwan_vix"]))
        for raw_key, score_key in (
            ("market_pe_median", "market_pe_percentile"),
            ("market_pb_median", "market_pb_percentile"),
            ("market_dividend_yield_median", "dividend_yield_percentile"),
        ):
            if current.get(raw_key) is not None:
                historical = [row.get("features", {}).get(raw_key) for row in history]
                result[score_key] = percentile_rank(historical[-1260:], float(current[raw_key]))
        if current_close is not None and closes:
            high_52w = max((closes + [float(current_close)])[-252:])
            result["drawdown_52w"] = float(current_close) / high_52w - 1.0
        if history:
            previous_down = float(history[-1].get("limit_down_ratio") or 0)
            result["limit_contraction"] = previous_down > 0 and limits.limit_down_ratio <= previous_down * 0.5
            previous_close = history[-1].get("taiex_close")
            result["price_reversal"] = bool(previous_close and current_close and float(current_close) > float(previous_close))
            result["breadth_reversal"] = float(current.get("advance_decline_ratio") or 0) > 0
            result["institution_reversal"] = float(current.get("foreign_flow_ratio") or 0) > 0
            result["futures_reversal"] = float(current.get("futures_basis_pct") or 0) > 0
            result["options_reversal"] = float(zones.get("pressure_balance") or 0) > 0
        return result

    def persist(self, snapshot: MarketSnapshot, write_history: bool = True) -> dict[str, Any]:
        payload = snapshot.to_dict()
        self.docs.mkdir(parents=True, exist_ok=True)
        write_json(self.docs / "latest.json", payload)
        if snapshot.report_mode == "close":
            write_json(self.docs / "close-latest.json", payload)
        archive = self.docs / "archive" / f"{snapshot.trade_date}-{snapshot.report_mode}.json"
        write_json(archive, payload)
        compact = {
            "trade_date": snapshot.trade_date,
            "report_mode": snapshot.report_mode,
            "taiex_close": snapshot.features.get("taiex_close"),
            "limit_up_count": payload.get("limit_up_count"),
            "limit_down_count": payload.get("limit_down_count"),
            "limit_up_ratio": payload.get("limit_up_ratio"),
            "limit_down_ratio": payload.get("limit_down_ratio"),
            "composite_score": snapshot.composite_score,
            "technical_score": snapshot.technical_score,
            "technical_state": snapshot.technical_state,
            "domestic_market_state": snapshot.domestic_market_state,
            "reversal_stage": snapshot.reversal_stage,
            "features": snapshot.features,
            "limits": {key: value.to_dict() for key, value in snapshot.limits.items()},
        }
        if snapshot.report_mode == "close" and write_history and snapshot.features.get("core_data_ready") is True:
            upsert_history(self.history_path, compact)
        return payload

    @staticmethod
    def digest(payload: dict[str, Any]) -> str:
        relevant = {
            key: payload.get(key)
            for key in ("trade_date", "report_mode", "domestic_market_state", "overnight_risk_state", "composite_score", "technical_score", "technical_state", "reversal_stage", "model_exposure_range", "limit_up_count", "limit_down_count")
        }
        return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()
