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
    """Return every fields/data pair used by TWSE's old and new JSON shapes."""
    if not isinstance(payload, dict):
        return []
    result = [table for table in payload.get("tables", []) if isinstance(table, dict)]
    for key, fields in payload.items():
        match = re.fullmatch(r"fields(\d*)", str(key))
        if not match or not isinstance(fields, list):
            continue
        data = payload.get(f"data{match.group(1)}")
        if isinstance(data, list):
            result.append({"fields": fields, "data": data, "title": payload.get(f"title{match.group(1)}", "")})
    return result


def _all_rows(payload: Any) -> list[dict[str, Any]]:
    return [
        row
        for table in _tables(payload)
        for row in row_objects(table.get("fields") or [], table.get("data") or [])
    ]


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
            # The OpenAPI schema has also used machine-readable property names.
            # Its documented column order is stable, so use a guarded positional
            # fallback when the Chinese labels are absent.
            if not eligible and rows and isinstance(rows[0], dict):
                values = list(rows[0].values())
                if len(values) >= 14:
                    close_candidate = number(values[6])
                    candidates = [int(number(values[index], 0) or 0) for index in (8, 9, 10, 11, 12)]
                    if close_candidate is not None and all(value >= 0 for value in candidates):
                        features["otc_close"] = features.get("otc_close") or close_candidate
                        up, up_limit, down, down_limit, flat = candidates
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
            message = "" if eligible else "回傳成功，但找不到上漲、下跌與平盤家數欄位"
            statuses.append(SourceStatus("TPEx市場現況", "ready" if eligible else "partial", ymd, message, url))
        except Exception as error:
            statuses.append(SourceStatus("TPEx市場現況", "partial", ymd, str(error), url))

    def _collect_institutional(self, ymd: str, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["twse_institutional"].format(date=ymd)
        try:
            payload = self.client.get_json(url)
            if str(payload.get("stat", "")).upper() not in {"OK", ""}:
                raise SourceError(str(payload.get("stat")))
            rows = _all_rows(payload)
            total_turnover = number(features.get("market_turnover"))
            if total_turnover is None or total_turnover <= 0:
                raise SourceError("無法取得上市市場成交值，法人買賣超占比不能計算")

            totals: dict[str, float] = {}
            dealer_parts: list[float] = []
            dealer_total: float | None = None
            for row in rows:
                label = str(find_field(row, "單位名稱") or next(iter(row.values()), "")).strip()
                net = number(
                    find_field(row, "買賣差額")
                    or find_field(row, "買賣超金額")
                    or find_field(row, "買賣超")
                )
                if net is None:
                    buy = number(find_field(row, "買進金額"))
                    sell = number(find_field(row, "賣出金額"))
                    if buy is not None and sell is not None:
                        net = buy - sell
                if net is None or label == "合計":
                    continue

                # 外資自營商已計入自營商，不能覆蓋「外資及陸資」的數值。
                if "外資及陸資" in label and "外資自營商" not in label:
                    totals["foreign_flow_ratio"] = net
                elif "投信" in label:
                    totals["trust_flow_ratio"] = net
                elif "自營商" in label and "外資自營商" not in label:
                    if "合計" in label:
                        dealer_total = net
                    else:
                        dealer_parts.append(net)

            if dealer_total is not None:
                totals["dealer_flow_ratio"] = dealer_total
            elif dealer_parts:
                totals["dealer_flow_ratio"] = sum(dealer_parts)

            for key, net in totals.items():
                features[key] = net / total_turnover
            features["institutional_flow_parser_version"] = "bfi82u_v2"

            expected = {"foreign_flow_ratio", "trust_flow_ratio", "dealer_flow_ratio"}
            missing = sorted(expected - totals.keys())
            display_names = {
                "foreign_flow_ratio": "外資及陸資",
                "trust_flow_ratio": "投信",
                "dealer_flow_ratio": "自營商",
            }
            message = "" if not missing else "回傳成功，但缺少法人欄位：" + "、".join(
                display_names[name] for name in missing
            )
            statuses.append(SourceStatus("TWSE三大法人", "ready" if not missing else "partial", ymd, message, url))
        except Exception as error:
            statuses.append(SourceStatus("TWSE三大法人", "partial", ymd, str(error), url))

    def _collect_margin(self, ymd: str, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["twse_margin"].format(date=ymd)
        try:
            payload = self.client.get_json(url)
            values: list[float] = []
            for row in _all_rows(payload):
                label = " ".join(map(str, row.values()))
                if "融資" in label and ("金額" in label or "仟元" in label or "千元" in label):
                    value = number(find_field(row, "今日餘額") or find_field(row, "本日餘額"))
                    if value is not None:
                        values.append(value)
            if values:
                features["margin_balance"] = max(values)
            message = "" if values else "回傳成功，但找不到融資金額的今日餘額欄位"
            statuses.append(SourceStatus("TWSE融資融券", "ready" if values else "partial", ymd, message, url))
        except Exception as error:
            statuses.append(SourceStatus("TWSE融資融券", "partial", ymd, str(error), url))

    def _collect_lending(self, ymd: str, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["twse_lending"].format(date=ymd)
        try:
            payload = self.client.get_json(url)
            rows = _all_rows(payload)
            balances = [
                number(find_field(row, "今日借券餘額") or find_field(row, "本日借券餘額"))
                for row in rows
            ]
            clean = [value for value in balances if value is not None]
            if clean:
                features["borrowed_balance"] = sum(clean)
            message = "" if clean else "TWT72U是借券餘額資料；回傳中找不到今日借券餘額欄位"
            statuses.append(SourceStatus("TWSE借券餘額", "ready" if clean else "partial", ymd, message, url))
        except Exception as error:
            statuses.append(SourceStatus("TWSE借券餘額", "partial", ymd, str(error), url))

    def _collect_valuation(self, ymd: str, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["twse_valuation"].format(date=ymd)
        try:
            payload = self.client.get_json(url)
            rows = _all_rows(payload)
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
            message = "" if pe_values else "回傳成功，但找不到本益比欄位"
            statuses.append(SourceStatus("TWSE估值", "ready" if pe_values else "partial", ymd, message, url))
        except Exception as error:
            statuses.append(SourceStatus("TWSE估值", "partial", ymd, str(error), url))
