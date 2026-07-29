from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SourceStatus:
    name: str
    status: str
    as_of: str | None = None
    message: str = ""
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LimitStats:
    market: str
    eligible_count: int = 0
    limit_up_count: int = 0
    limit_down_count: int = 0
    intraday_up_touch_count: int | None = None
    intraday_down_touch_count: int | None = None
    limit_up_ratio: float = 0.0
    limit_down_ratio: float = 0.0
    limit_breadth: float = 0.0
    strength_ratio: float = 1.0
    up_percentile_1y: float | None = None
    down_percentile_1y: float | None = None
    up_percentile_5y: float | None = None
    down_percentile_5y: float | None = None
    up_percentile_full: float | None = None
    down_percentile_full: float | None = None
    up_historical_rank: int | None = None
    down_historical_rank: int | None = None
    universe_verified: bool = False
    calculation_method: str = "unverified"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MarketSnapshot:
    trade_date: str
    report_mode: str
    generated_at: str
    domestic_market_state: str = "盤整"
    overnight_risk_state: str = "中性"
    composite_score: float = 50.0
    confidence: str = "低"
    model_exposure_range: list[int] = field(default_factory=lambda: [40, 60])
    shadow_mode: bool = True
    module_scores: dict[str, float] = field(default_factory=dict)
    module_coverage: dict[str, float] = field(default_factory=dict)
    features: dict[str, float | str | None] = field(default_factory=dict)
    limits: dict[str, LimitStats] = field(default_factory=dict)
    historical_limit_analogs: list[dict[str, Any]] = field(default_factory=list)
    post_analog_returns: dict[str, Any] = field(default_factory=dict)
    options_pressure_zones: dict[str, Any] = field(default_factory=dict)
    positive_drivers: list[str] = field(default_factory=list)
    negative_drivers: list[str] = field(default_factory=list)
    reversal_stage: str = "無"
    reversal_reasons: list[str] = field(default_factory=list)
    data_freshness: dict[str, str] = field(default_factory=dict)
    source_status: list[SourceStatus] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["limits"] = {k: v.to_dict() for k, v in self.limits.items()}
        result["source_status"] = [s.to_dict() for s in self.source_status]
        combined = self.limits.get("combined", LimitStats("combined"))
        result.update(
            {
                "limit_up_count": combined.limit_up_count,
                "limit_down_count": combined.limit_down_count,
                "limit_up_ratio": combined.limit_up_ratio,
                "limit_down_ratio": combined.limit_down_ratio,
                "limit_breadth": combined.limit_breadth,
                "limit_up_percentile_1y": combined.up_percentile_1y,
                "limit_down_percentile_1y": combined.down_percentile_1y,
                "limit_up_percentile_5y": combined.up_percentile_5y,
                "limit_down_percentile_5y": combined.down_percentile_5y,
                "limit_up_percentile_full": combined.up_percentile_full,
                "limit_down_percentile_full": combined.down_percentile_full,
                "limit_up_historical_rank": combined.up_historical_rank,
                "limit_down_historical_rank": combined.down_historical_rank,
                "historical_limit_analogs": self.historical_limit_analogs,
                "post_analog_returns": self.post_analog_returns,
                "intraday_limit_touch_counts": {
                    "up": combined.intraday_up_touch_count,
                    "down": combined.intraday_down_touch_count,
                },
                "volume_regime": self.features.get("volume_price_confirmation"),
                "capital_rotation": self.features.get("rotation_score"),
                "futures_basis": {
                    "points": self.features.get("futures_basis"),
                    "percent": self.features.get("futures_basis_pct"),
                },
                "non_institutional_position_proxy": {
                    "short_long_ratio": self.features.get("noninst_short_long_ratio"),
                    "label": "非三大法人部位代理",
                },
                "taiwan_vix": self.features.get("taiwan_vix"),
                "us_vix": self.features.get("us_vix"),
                "tsm_adr_premium": self.features.get("tsm_adr_premium"),
                "sox_change": {
                    "1d": self.features.get("sox_return_1d"),
                    "5d": self.features.get("sox_return_5d"),
                    "20d": self.features.get("sox_return_20d"),
                },
                "margin_stress": {
                    "proxy": self.features.get("margin_stress_proxy"),
                    "percentile": self.features.get("margin_stress_percentile"),
                },
                "securities_lending": {
                    "borrowed_balance": self.features.get("borrowed_balance"),
                    "borrowed_sell_balance": self.features.get("borrowed_sell_balance"),
                    "borrowed_sell_change": self.features.get("borrowed_sell_5d_change"),
                },
            }
        )
        return result
