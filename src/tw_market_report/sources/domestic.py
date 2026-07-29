from __future__ import annotations

import re
import statistics
from datetime import date
from typing import Any

from ..models import LimitStats, SourceStatus
from ..stats import safe_div
from .http import HttpClient, SourceError
from .parsing import find_field, number, row_objects


PAREN_COUNT_RE = re.compile(r"([\d,]+)\s*\(([\d,]+)\)")


def _count_pair(value: Any) -> tuple[int, int]:
    match = PAREN_COUNT_RE.search(str(value))
    if not match:
        return int(number(value, 0) or 0), 0
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


def _tables(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("tables"), list):
        return payload["tables"]
    return []


class DomesticCollector:
    def __init__(self, sources: dict[str, str], client: HttpClient | None = None) -> None:
        self.sources = sources
        self.client = client or HttpClient()

    def collect(self, trade_date: date) -> tuple[dict[str, Any], dict[str, LimitStats], list[SourceStatus]]:
        ymd = trade_date.strftime("%Y%m%d")
        features: dict[str, Any] = {}
        limits: dict[str, LimitStats] = {}
        statuses: list[SourceStatus] = []
        self._collect_twse(ymd, features, limits, statuses)
        self._collect_tpex(ymd, features, limits, statuses)
        self._collect_institutional(ymd, features, statuses)
        self._collect_margin(ymd, features, statuses)
        self._collect_lending(ymd, features, statuses)
        self._collect_valuation(ymd, features, statuses)
        return features, limits, statuses

    def collect_twse_only(self, trade_date: date) -> tuple[dict[str, Any], LimitStats | None, SourceStatus]:
        features: dict[str, Any] = {}
        limits: dict[str, LimitStats] = {}
        statuses: list[SourceStatus] = []
        self._collect_twse(trade_date.strftime("%Y%m%d"), features, limits, statuses)
        return features, limits.get("twse"), statuses[-1]

    def _collect_twse(self, ymd: str, features: dict, limits: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["twse_mi_index"].format(date=ymd)
        try:
            payload = self.client.get_json(url)
            if str(payload.get("stat", "")).upper() not in {"OK", ""}:
                raise SourceError(str(payload.get("stat")))
            tables = _tables(payload)
            turnover_values: list[float] = []
            for table in tables:
                fields = table.get("fields") or []
                rows = row_objects(fields, table.get("data") or []) if fields else []
                for row in rows:
                    label = " ".join(str(value) for value in row.values())
                    if "發行量加權股價指數" in label:
                        features["taiex_close"] = number(find_field(row, "收盤")) or number(list(row.values())[-2])
                    code = str(find_field(row, "證券代號") or "").strip()
                    if code:
                        turnover = number(find_field(row, "成交金額"))
                        if turnover is not None:
                            turnover_values.append(turnover)
                    if code == "2330":
                        features["tsmc_close"] = number(find_field(row, "收盤"))
                        features["tsmc_turnover"] = number(find_field(row, "成交金額"))
                title = str(table.get("title", ""))
                if "漲跌證券數" in title or any("上漲(漲停)" in "".join(map(str, row)) for row in table.get("data") or []):
                    up_total = down_total = unchanged = 0
                    up_limit = down_limit = 0
                    for raw in table.get("data") or []:
                        label = str(raw[0]) if raw else ""
                        value = raw[-1] if raw else "0"
                        if "上漲" in label:
                            up_total, up_limit = _count_pair(value)
                        elif "下跌" in label:
                            down_total, down_limit = _count_pair(value)
                        elif "持平" in label or "未漲跌" in label:
                            unchanged = int(number(value, 0) or 0)
                    eligible = up_total + down_total + unchanged
                    limits["twse"] = LimitStats(
                        market="twse",
                        eligible_count=eligible,
                        limit_up_count=up_limit,
                        limit_down_count=down_limit,
                        limit_up_ratio=safe_div(up_limit, eligible),
                        limit_down_ratio=safe_div(down_limit, eligible),
                        limit_breadth=safe_div(up_limit - down_limit, eligible),
                        strength_ratio=(up_limit + 1) / (down_limit + 1),
                        universe_verified=False,
                        calculation_method="official_market_aggregate",
                    )
                    features["advance_decline_ratio"] = safe_div(up_total - down_total, eligible)
            if turnover_values:
                features["market_turnover"] = sum(turnover_values)
            if features.get("taiex_close") is None:
                raise SourceError("TAIEX close was not present")
            statuses.append(SourceStatus("TWSE收盤行情", "ready", ymd, url=url))
        except Exception as error:
            statuses.append(SourceStatus("TWSE收盤行情", "blocked", ymd, str(error), url))

    def _collect_tpex(self, ymd: str, features: dict, limits: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["tpex_highlight"]
        try:
            payload = self.client.get_json(url)
            rows = payload if isinstance(payload, list) else payload.get("data", [])
            merged: dict[str, Any] = {}
            for row in rows:
                if isinstance(row, dict):
                    merged.update(row)
            features["otc_close"] = number(
                find_field(merged, "櫃買", "指數") or find_field(merged, "收盤", "指數") or find_field(merged, "收市")
            )
            up = int(number(find_field(merged, "上漲", "家數") or find_field(merged, "上漲"), 0) or 0)
            down = int(number(find_field(merged, "下跌", "家數") or find_field(merged, "下跌"), 0) or 0)
            flat = int(number(find_field(merged, "平盤", "家數") or find_field(merged, "平盤"), 0) or 0)
            up_limit = int(number(find_field(merged, "漲停"), 0) or 0)
            down_limit = int(number(find_field(merged, "跌停"), 0) or 0)
            eligible = up + down + flat
            if eligible:
                limits["tpex"] = LimitStats(
                    market="tpex",
                    eligible_count=eligible,
                    limit_up_count=up_limit,
                    limit_down_count=down_limit,
                    limit_up_ratio=safe_div(up_limit, eligible),
                    limit_down_ratio=safe_div(down_limit, eligible),
                    limit_breadth=safe_div(up_limit - down_limit, eligible),
                    strength_ratio=(up_limit + 1) / (down_limit + 1),
                    universe_verified=False,
                    calculation_method="official_market_highlight",
                )
            statuses.append(SourceStatus("TPEx市場現況", "ready" if eligible else "partial", ymd, url=url))
        except Exception as error:
            statuses.append(SourceStatus("TPEx市場現況", "partial", ymd, str(error), url))

    def _collect_institutional(self, ymd: str, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["twse_institutional"].format(date=ymd)
        try:
            payload = self.client.get_json(url)
            fields = payload.get("fields", [])
            rows = row_objects(fields, payload.get("data", []))
            total_turnover = float(features.get("market_turnover") or 1.0)
            mapping = {"外資": "foreign_flow_ratio", "投信": "trust_flow_ratio", "自營商": "dealer_flow_ratio"}
            for row in rows:
                label = str(next(iter(row.values()), ""))
                net = number(find_field(row, "買賣超"), 0) or 0
                for needle, key in mapping.items():
                    if needle in label:
                        features[key] = net / total_turnover
            statuses.append(SourceStatus("TWSE三大法人", "ready", ymd, url=url))
        except Exception as error:
            statuses.append(SourceStatus("TWSE三大法人", "partial", ymd, str(error), url))

    def _collect_margin(self, ymd: str, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["twse_margin"].format(date=ymd)
        try:
            payload = self.client.get_json(url)
            tables = _tables(payload)
            values: list[float] = []
            for table in tables:
                for row in row_objects(table.get("fields", []), table.get("data", [])):
                    if "融資" in " ".join(map(str, row.values())):
                        value = number(find_field(row, "今日餘額"))
                        if value is not None:
                            values.append(value)
            if values:
                features["margin_balance"] = max(values)
            statuses.append(SourceStatus("TWSE融資融券", "ready" if values else "partial", ymd, url=url))
        except Exception as error:
            statuses.append(SourceStatus("TWSE融資融券", "partial", ymd, str(error), url))

    def _collect_lending(self, ymd: str, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["twse_lending"].format(date=ymd)
        try:
            payload = self.client.get_json(url)
            fields = payload.get("fields", []) if isinstance(payload, dict) else []
            rows = row_objects(fields, payload.get("data", [])) if fields else []
            balances = [number(find_field(row, "借券賣出", "餘額")) for row in rows]
            clean = [value for value in balances if value is not None]
            if clean:
                features["borrowed_sell_balance"] = sum(clean)
            statuses.append(SourceStatus("TWSE借券賣出", "ready" if clean else "partial", ymd, url=url))
        except Exception as error:
            statuses.append(SourceStatus("TWSE借券賣出", "partial", ymd, str(error), url))

    def _collect_valuation(self, ymd: str, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["twse_valuation"].format(date=ymd)
        try:
            payload = self.client.get_json(url)
            fields = payload.get("fields", [])
            rows = row_objects(fields, payload.get("data", []))
            pe_values: list[float] = []
            pb_values: list[float] = []
            yield_values: list[float] = []
            for row in rows:
                pe = number(find_field(row, "本益比"))
                pb = number(find_field(row, "股價淨值比"))
                dividend_yield = number(find_field(row, "殖利率"))
                if pe is not None and pe > 0:
                    pe_values.append(pe)
                if pb is not None and pb > 0:
                    pb_values.append(pb)
                if dividend_yield is not None and dividend_yield >= 0:
                    yield_values.append(dividend_yield)
                if str(find_field(row, "證券代號") or "").strip() == "2330":
                    features["tsmc_pe"] = pe
                    features["tsmc_pb"] = pb
                    features["tsmc_dividend_yield"] = dividend_yield
            if pe_values:
                features["market_pe_median"] = statistics.median(pe_values)
            if pb_values:
                features["market_pb_median"] = statistics.median(pb_values)
            if yield_values:
                features["market_dividend_yield_median"] = statistics.median(yield_values)
            statuses.append(SourceStatus("TWSE估值", "ready" if pe_values else "partial", ymd, url=url))
        except Exception as error:
            statuses.append(SourceStatus("TWSE估值", "partial", ymd, str(error), url))
