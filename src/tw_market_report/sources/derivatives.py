from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..models import SourceStatus
from ..stats import safe_div
from .http import HttpClient
from .parsing import number, parse_tables


def _date_text(value: str) -> str:
    return value.replace("/", "-")[:10]


class DerivativesCollector:
    def __init__(self, sources: dict[str, str], client: HttpClient | None = None) -> None:
        self.sources = sources
        self.client = client or HttpClient()

    def collect(self, trade_date: date, spot: float | None) -> tuple[dict[str, Any], dict[str, Any], list[SourceStatus]]:
        features: dict[str, Any] = {}
        zones: dict[str, Any] = {"calls": [], "puts": [], "max_pain": None, "status": "partial"}
        statuses: list[SourceStatus] = []
        self._pc_ratio(features, statuses)
        self._taiwan_vix(features, statuses)
        self._futures(features, statuses, spot)
        self._institution_positions(features, statuses)
        self._options(zones, statuses, spot)
        return features, zones, statuses

    def _pc_ratio(self, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["taifex_pc_ratio"]
        try:
            tables = parse_tables(self.client.get_text(url))
            candidates = []
            for table in tables:
                for row in table:
                    if len(row) >= 7 and "/" in row[0] and number(row[3]) is not None:
                        candidates.append(row)
            if not candidates:
                raise ValueError("Put/Call table not found")
            row = candidates[-1]
            volume_ratio = number(row[3])
            oi_ratio = number(row[6])
            features["put_call_volume_ratio"] = (volume_ratio or 100) / 100.0
            features["put_call_oi_ratio"] = (oi_ratio or 100) / 100.0
            # Centered interpretation; extremes remain diagnostic rather than directional.
            features["put_call_sentiment"] = 1.0 - min(abs(features["put_call_oi_ratio"] - 1.0), 1.0)
            statuses.append(SourceStatus("TAIFEX Put/Call", "ready", _date_text(row[0]), url=url))
        except Exception as error:
            statuses.append(SourceStatus("TAIFEX Put/Call", "partial", message=str(error), url=url))

    def _taiwan_vix(self, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["taiwan_vix"]
        try:
            tables = parse_tables(self.client.get_text(url))
            candidates: list[tuple[str, float]] = []
            for table in tables:
                for row in table:
                    if row and "/" in row[0]:
                        values = [number(cell) for cell in row[1:]]
                        clean = [value for value in values if value is not None]
                        if clean:
                            candidates.append((row[0], clean[-1]))
            if not candidates:
                raise ValueError("TAIWAN VIX value not found")
            as_of, value = candidates[-1]
            features["taiwan_vix"] = value
            statuses.append(SourceStatus("TAIWAN VIX", "ready", _date_text(as_of), url=url))
        except Exception as error:
            statuses.append(SourceStatus("TAIWAN VIX", "partial", message=str(error), url=url))

    def _futures(self, features: dict, statuses: list[SourceStatus], spot: float | None) -> None:
        url = self.sources["taifex_daily_futures"]
        try:
            tables = parse_tables(self.client.get_text(url))
            candidates: list[dict[str, Any]] = []
            for table in tables:
                for row in table:
                    if len(row) >= 8 and row[0].strip() in {"TX", "臺股期貨"}:
                        nums = [number(cell) for cell in row]
                        candidates.append({"row": row, "numbers": nums})
            if not candidates:
                raise ValueError("TX daily row not found")
            nums = candidates[0]["numbers"]
            clean = [value for value in nums[1:] if value is not None]
            settlement = clean[4] if len(clean) > 4 else clean[-1]
            features["tx_settlement"] = settlement
            if spot:
                features["futures_basis"] = settlement - spot
                features["futures_basis_pct"] = (settlement - spot) / spot
            statuses.append(SourceStatus("TAIFEX台指期行情", "ready", url=url))
        except Exception as error:
            statuses.append(SourceStatus("TAIFEX台指期行情", "partial", message=str(error), url=url))

    def _institution_positions(self, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["taifex_futures"]
        try:
            tables = parse_tables(self.client.get_text(url))
            text_rows = [row for table in tables for row in table]
            foreign_nets: list[float] = []
            noninst_ratios: list[float] = []
            for row in text_rows:
                joined = " ".join(row)
                if any(product in joined for product in ("臺股期貨", "小型臺指期貨", "微型臺指期貨")) and "外資" in joined:
                    nums = [number(cell) for cell in row]
                    clean = [value for value in nums if value is not None]
                    if clean:
                        foreign_nets.append(clean[-1])
                if any(product in joined for product in ("小型臺指期貨", "微型臺指期貨")):
                    nums = [number(cell) for cell in row]
                    clean = [value for value in nums if value is not None]
                    if len(clean) >= 2 and clean[-2] != 0:
                        noninst_ratios.append(safe_div(clean[-1], clean[-2], 1.0))
            if foreign_nets:
                features["foreign_futures_net"] = sum(foreign_nets)
                features["foreign_futures_net_ratio"] = sum(foreign_nets) / max(sum(abs(v) for v in foreign_nets), 1.0)
            if noninst_ratios:
                features["noninst_short_long_ratio"] = sum(noninst_ratios) / len(noninst_ratios)
            statuses.append(SourceStatus("TAIFEX三大法人期貨", "ready" if foreign_nets else "partial", url=url))
        except Exception as error:
            statuses.append(SourceStatus("TAIFEX三大法人期貨", "partial", message=str(error), url=url))

    def _options(self, zones: dict, statuses: list[SourceStatus], spot: float | None) -> None:
        url = self.sources["taifex_options"]
        try:
            tables = parse_tables(self.client.get_text(url))
            records: list[dict[str, Any]] = []
            side = None
            for table in tables:
                for row in table:
                    joined = " ".join(row)
                    if "買權" in joined and len(row) <= 3:
                        side = "call"
                        continue
                    if "賣權" in joined and len(row) <= 3:
                        side = "put"
                        continue
                    if side and len(row) >= 8:
                        strike = number(row[2])
                        oi = number(row[-1])
                        if strike and oi is not None and (not spot or abs(strike / spot - 1) <= 0.10):
                            records.append({"side": side, "strike": strike, "open_interest": oi, "weight": oi})
            if not records:
                raise ValueError("option strike/open-interest rows not found")
            for side, target in (("call", "calls"), ("put", "puts")):
                ranked = sorted((r for r in records if r["side"] == side), key=lambda r: r["weight"], reverse=True)[:3]
                zones[target] = ranked
            strikes = sorted({r["strike"] for r in records})
            if strikes:
                payouts = []
                for settlement in strikes:
                    payout = sum(
                        max(0.0, settlement - r["strike"]) * r["open_interest"] if r["side"] == "call"
                        else max(0.0, r["strike"] - settlement) * r["open_interest"]
                        for r in records
                    )
                    payouts.append((payout, settlement))
                zones["max_pain"] = min(payouts)[1]
            zones["status"] = "ready"
            if zones["calls"] and zones["puts"] and spot:
                call_weight = sum(r["weight"] for r in zones["calls"])
                put_weight = sum(r["weight"] for r in zones["puts"])
                zones["pressure_balance"] = safe_div(put_weight - call_weight, put_weight + call_weight)
            statuses.append(SourceStatus("TAIFEX選擇權履約價", "ready", url=url))
        except Exception as error:
            statuses.append(SourceStatus("TAIFEX選擇權履約價", "partial", message=str(error), url=url))
