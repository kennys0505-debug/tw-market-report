from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .models import LimitStats, SourceStatus


def fixture_history(sessions: int = 320) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current = date(2025, 4, 1)
    index_value = 20500.0
    for i in range(sessions):
        while current.weekday() >= 5:
            current += timedelta(days=1)
        cycle = math.sin(i / 18.0)
        shock = -0.045 if i in {75, 76, 210} else 0.0
        daily_return = 0.0005 + 0.004 * math.sin(i / 7.0) + shock
        index_value *= 1.0 + daily_return
        down_ratio = max(0.0, 0.003 - daily_return * 0.3 + (0.05 if shock else 0.0))
        up_ratio = max(0.0, 0.004 + daily_return * 0.35)
        features = {
            "taiex_daily_return_proxy": daily_return,
            "otc_daily_return_proxy": daily_return * 1.1,
            "tpex_breadth_proxy": max(-1.0, min(1.0, daily_return * 35)),
            "limit_strength_proxy": math.log((up_ratio + 1 / 1800) / (down_ratio + 1 / 1800)),
            "taiex_ma20_gap": 0.02 * cycle + daily_return,
            "taiex_ma60_gap": 0.03 * math.sin(i / 34.0),
            "otc_ma20_gap": 0.025 * math.sin(i / 16.0),
            "advance_decline_ratio": max(-1.0, min(1.0, daily_return * 30)),
            "above_ma20_ratio": 0.5 + 0.35 * cycle,
            "volume_price_confirmation": daily_return * (1.0 + abs(cycle)),
            "limit_breadth": up_ratio - down_ratio,
            "foreign_flow_ratio": daily_return * 0.12,
            "trust_flow_ratio": daily_return * 0.06,
            "dealer_flow_ratio": daily_return * 0.03,
            "rotation_score": 0.5 + 0.3 * math.sin(i / 11.0),
            "margin_20d_change": 0.01 + 0.03 * math.sin(i / 25.0),
            "margin_daily_change_proxy": daily_return * 0.4,
            "margin_price_divergence_proxy": -daily_return * 0.6,
            "borrowed_balance_daily_change_proxy": -daily_return * 0.2,
            "margin_turnover_pressure_proxy": 0.18 + 0.02 * cycle,
            "margin_price_divergence": max(0.0, -daily_return) * 2,
            "borrowed_sell_5d_change": -daily_return * 0.8,
            "margin_stress_proxy": 1.55 + 0.12 * cycle,
            "futures_basis_pct": daily_return * 0.25,
            "foreign_futures_net": cycle * 20000,
            "futures_market_oi": 200000 + cycle * 10000,
            "foreign_futures_scheme7_score": 50 + cycle * 25,
            "noninst_short_long_ratio": 1.0 - cycle * 0.15,
            "put_call_sentiment": 0.5 + cycle * 0.2,
            "taiwan_vix_change_5d": -daily_return * 4,
            "taiwan_vix_level_proxy": 20.0 - daily_return * 100,
            "option_pressure_balance": cycle * 0.25,
            "sox_return_5d": daily_return * 4,
            "sox_relative_nasdaq": daily_return,
            "tsm_adr_premium": daily_return * 2,
            "us_vix_change_5d": -daily_return * 3,
            "usd_twd_change_5d": -daily_return * 0.3,
            "market_pe_percentile": 50 + cycle * 30,
            "market_pb_percentile": 50 + cycle * 25,
            "dividend_yield_percentile": 50 - cycle * 20,
            "drawdown_52w": -max(0.0, -cycle * 0.12),
        }
        rows.append(
            {
                "trade_date": current.isoformat(),
                "report_mode": "close",
                "taiex_close": round(index_value, 2),
                "limit_up_count": round(up_ratio * 1800),
                "limit_down_count": round(down_ratio * 1800),
                "limit_up_ratio": up_ratio,
                "limit_down_ratio": down_ratio,
                "composite_score": 50 + cycle * 18,
                "domestic_market_state": "轉多" if cycle > 0.55 else "轉空" if cycle < -0.55 else "盤整",
                "reversal_stage": "無",
                "features": features,
            }
        )
        current += timedelta(days=1)
    return rows


def fixture_current() -> tuple[dict[str, Any], dict[str, LimitStats], list[SourceStatus], dict[str, Any]]:
    history = fixture_history()
    latest = history[-1]
    features = dict(latest["features"])
    features.update(
        {
            "taiex_close": latest["taiex_close"],
            "otc_close": 286.4,
            "tsmc_close": 1165.0,
            "taiwan_vix": 24.8,
            "us_vix": 18.6,
            "sox_return_1d": -0.012,
            "sox_return_5d": 0.018,
            "sox_return_20d": 0.064,
            "tsm_adr_close": 191.2,
            "usd_twd": 30.41,
            "tsm_adr_premium": -0.0015,
            "put_call_volume_ratio": 1.07,
            "put_call_oi_ratio": 1.18,
            "futures_basis": -42.0,
            "futures_basis_pct": -42.0 / latest["taiex_close"],
            "foreign_futures_net": -18500,
            "borrowed_sell_balance": 9480000,
            "margin_balance": 328000000,
        }
    )
    twse = LimitStats("twse", 1010, 14, 7, 9, 5, 14 / 1010, 7 / 1010, 7 / 1010, 15 / 8)
    tpex = LimitStats("tpex", 820, 21, 12, 13, 8, 21 / 820, 12 / 820, 9 / 820, 22 / 13)
    twse.universe_verified = tpex.universe_verified = True
    twse.calculation_method = tpex.calculation_method = "fixture_official_per_security_limit_price"
    statuses = [
        SourceStatus("TWSE收盤行情", "fixture", latest["trade_date"], "示範資料"),
        SourceStatus("TPEx市場現況", "fixture", latest["trade_date"], "示範資料"),
        SourceStatus("TAIFEX期權", "fixture", latest["trade_date"], "示範資料"),
        SourceStatus("海外行情", "fixture", latest["trade_date"], "示範資料"),
    ]
    zones = {
        "status": "fixture",
        "calls": [{"strike": 23800, "open_interest": 28600}, {"strike": 24000, "open_interest": 25100}],
        "puts": [{"strike": 23000, "open_interest": 31200}, {"strike": 22800, "open_interest": 22300}],
        "max_pain": 23400,
        "pressure_balance": 0.08,
    }
    return features, {"twse": twse, "tpex": tpex}, statuses, zones
