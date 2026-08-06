from __future__ import annotations

import bisect
import csv
import io
import re
import time
import urllib.parse
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ReportConfig
from .history import upsert_history
from .limits import combine_limit_stats
from .models import LimitStats, SourceStatus
from .sources.derivatives import DerivativesCollector
from .sources.domestic import DomesticCollector
from .sources.http import HttpClient
from .sources.parsing import number, row_objects
from .stats import safe_div


COMMON_CODE = re.compile(r"^[1-9]\d{3}$")
YAHOO_SYMBOLS = {"sox": "^SOX", "nasdaq": "^IXIC", "tsm": "TSM", "usd_twd": "TWD=X"}
TPEX_HISTORICAL_FIELDS = [
    "代號",
    "名稱",
    "收盤",
    "漲跌",
    "開盤",
    "最高",
    "最低",
    "均價",
    "成交股數",
    "成交金額(元)",
    "成交筆數",
    "最後買價",
    "最後買量(千股)",
    "最後賣價",
    "最後賣量(千股)",
    "發行股數",
    "次日參考價",
    "次日漲停價",
    "次日跌停價",
]


def roc_date(value: date) -> str:
    return f"{value.year - 1911}/{value.month:02d}/{value.day:02d}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value.strip()[:10], pattern).date()
        except ValueError:
            continue
    return None


class HistoricalOverseasCache:
    """Bulk-load free daily overseas series and align them without look-ahead."""

    def __init__(self, client: HttpClient, sources: dict[str, str], start: date, end: date) -> None:
        self.client = client
        self.sources = sources
        self.start = start - timedelta(days=45)
        self.end = end + timedelta(days=2)
        self.series: dict[str, list[tuple[date, float]]] = {}
        self.errors: dict[str, str] = {}
        self._load_vix()
        for name, symbol in YAHOO_SYMBOLS.items():
            self._load_yahoo(name, symbol)

    def _load_vix(self) -> None:
        try:
            rows = csv.DictReader(io.StringIO(self.client.get_text(self.sources["cboe_vix_csv"])))
            values: list[tuple[date, float]] = []
            for row in rows:
                session = _parse_date(row.get("DATE"))
                close = number(row.get("CLOSE"))
                if session and close is not None and self.start <= session <= self.end:
                    values.append((session, close))
            if values:
                self.series["vix"] = sorted(dict(values).items())
            else:
                self.errors["vix"] = "no Cboe VIX rows in requested period"
        except Exception as error:
            self.errors["vix"] = str(error)

    def _load_yahoo(self, name: str, symbol: str) -> None:
        base = self.sources["yahoo_chart"].split("?", 1)[0].format(
            symbol=urllib.parse.quote(symbol, safe="")
        )
        period1 = int(datetime.combine(self.start, dt_time.min, timezone.utc).timestamp())
        period2 = int(datetime.combine(self.end, dt_time.min, timezone.utc).timestamp())
        url = f"{base}?period1={period1}&period2={period2}&interval=1d&events=history"
        try:
            result = self.client.get_json(url)["chart"]["result"][0]
            timestamps = result.get("timestamp") or []
            closes = result["indicators"]["quote"][0].get("close") or []
            values = [
                (datetime.fromtimestamp(int(stamp), timezone.utc).date(), float(close))
                for stamp, close in zip(timestamps, closes)
                if close is not None
            ]
            if values:
                self.series[name] = sorted(dict(values).items())
            else:
                self.errors[name] = "empty Yahoo history"
        except Exception as error:
            self.errors[name] = str(error)

    def _point(self, name: str, target: date, strictly_before: bool) -> tuple[float, int, date] | None:
        observations = self.series.get(name) or []
        dates = [item[0] for item in observations]
        position = bisect.bisect_left(dates, target) if strictly_before else bisect.bisect_right(dates, target)
        index = position - 1
        if index < 0:
            return None
        session, value = observations[index]
        return value, index, session

    def _change(self, name: str, index: int, periods: int) -> float | None:
        values = self.series.get(name) or []
        if index < periods:
            return None
        previous = values[index - periods][1]
        return values[index][1] / previous - 1.0 if previous else None

    def features_for(self, trade_date: date, tsmc_close: float | None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        as_of: dict[str, str] = {}
        # The Taiwanese close occurs before the US session bearing the same date.
        # US equities and VIX therefore use the last strictly earlier US session.
        points: dict[str, tuple[float, int, date]] = {}
        for name in ("vix", "sox", "nasdaq", "tsm"):
            if point := self._point(name, trade_date, strictly_before=True):
                points[name] = point
                as_of[name] = point[2].isoformat()
        if point := self._point("usd_twd", trade_date, strictly_before=False):
            points["usd_twd"] = point
            as_of["usd_twd"] = point[2].isoformat()

        if "vix" in points:
            value, index, _ = points["vix"]
            result.update({
                "us_vix": value,
                "us_vix_change_1d": self._change("vix", index, 1),
                "us_vix_change_5d": self._change("vix", index, 5),
            })
        if "sox" in points:
            _, index, _ = points["sox"]
            result.update({
                "sox_return_1d": self._change("sox", index, 1),
                "sox_return_5d": self._change("sox", index, 5),
                "sox_return_20d": self._change("sox", index, 20),
            })
        if "nasdaq" in points and "sox" in points:
            sox5 = result.get("sox_return_5d")
            nasdaq5 = self._change("nasdaq", points["nasdaq"][1], 5)
            if sox5 is not None and nasdaq5 is not None:
                result["sox_relative_nasdaq"] = float(sox5) - nasdaq5
        if "usd_twd" in points:
            value, index, _ = points["usd_twd"]
            result["usd_twd"] = value
            result["usd_twd_change_5d"] = self._change("usd_twd", index, 5)
        if "tsm" in points:
            result["tsm_adr_close"] = points["tsm"][0]
        if tsmc_close and result.get("tsm_adr_close") and result.get("usd_twd"):
            equivalent = float(result["tsm_adr_close"]) * float(result["usd_twd"]) / 5.0
            result["tsm_adr_equivalent_twd"] = equivalent
            result["tsm_adr_premium"] = equivalent / tsmc_close - 1.0
        result["historical_overseas_as_of"] = as_of
        return result


class HistoryBackfiller:
    def __init__(
        self, config: ReportConfig, delay: float = 0.35, history_path: Path | None = None
    ) -> None:
        self.config = config
        self.client = HttpClient(timeout=25, retries=2)
        self.domestic = DomesticCollector(config.sources, self.client)
        self.derivatives = DerivativesCollector(config.sources, self.client)
        self.delay = delay
        self.history_path = history_path or config.root / "data" / "history.jsonl"

    def run(
        self, start: date, end: date, include_derivatives: bool = True, include_overseas: bool = True
    ) -> tuple[int, int]:
        if end < start:
            raise ValueError("end must be on or after start")
        overseas = HistoricalOverseasCache(self.client, self.config.sources, start, end) if include_overseas else None
        # Warm up the previous-day TPEx official limit-price map without persisting.
        cursor = start - timedelta(days=14)
        previous_limits: dict[str, tuple[float, float]] = {}
        written = skipped = 0
        while cursor <= end:
            if cursor.weekday() < 5:
                try:
                    current_rows = self._tpex_rows(cursor)
                    tpex_stats, next_limits = self._tpex_stats(current_rows, previous_limits)
                    previous_limits = next_limits or previous_limits
                    if cursor >= start:
                        features, twse_stats, domestic_statuses = self.domestic.collect_historical(cursor)
                        if twse_stats is None or features.get("taiex_close") is None:
                            skipped += 1
                            print(
                                f"backfill skip date={cursor.isoformat()} reason=twse_core_missing "
                                f"statuses={[status.to_dict() for status in domestic_statuses]}"
                            )
                        elif tpex_stats.eligible_count == 0:
                            skipped += 1
                            print(
                                f"backfill skip date={cursor.isoformat()} reason=tpex_universe_missing "
                                f"rows={len(current_rows)} previous_limits={len(previous_limits)} "
                                f"fields={list(current_rows[0]) if current_rows else []}"
                            )
                        else:
                            combined = combine_limit_stats([twse_stats, tpex_stats])
                            features.update(self._tpex_features(current_rows))
                            features["limit_breadth"] = combined.limit_breadth
                            if features.get("market_turnover") and features.get("tsmc_turnover") is not None:
                                features["rotation_score"] = 1.0 - min(
                                    max(float(features["tsmc_turnover"]) / float(features["market_turnover"]), 0.0), 1.0
                                )

                            derivative_statuses: list[SourceStatus] = []
                            zones: dict[str, Any] = {}
                            if include_derivatives:
                                raw_derivatives, raw_zones, derivative_statuses = self.derivatives.collect(
                                    cursor, float(features["taiex_close"])
                                )
                                features.update(self._fresh_derivative_features(cursor, raw_derivatives, derivative_statuses))
                                if self._source_ready(derivative_statuses, self.config.sources["taifex_options"], cursor):
                                    zones = raw_zones
                                    if raw_zones.get("pressure_balance") is not None:
                                        features["option_pressure_balance"] = raw_zones["pressure_balance"]
                            if overseas:
                                features.update(overseas.features_for(cursor, number(features.get("tsmc_close"))))

                            statuses = domestic_statuses + derivative_statuses
                            ready = sum(status.status == "ready" for status in statuses)
                            features.update({
                                "historical_backfill_version": "free_history_v1",
                                "historical_source_coverage": safe_div(ready, len(statuses)) if statuses else 0.0,
                                "core_data_ready": True,
                            })
                            row = {
                                "trade_date": cursor.isoformat(),
                                "report_mode": "close",
                                "limit_scope": "combined",
                                "taiex_close": features["taiex_close"],
                                "limit_up_count": combined.limit_up_count,
                                "limit_down_count": combined.limit_down_count,
                                "limit_up_ratio": combined.limit_up_ratio,
                                "limit_down_ratio": combined.limit_down_ratio,
                                "composite_score": 50.0,
                                "domestic_market_state": "盤整",
                                "reversal_stage": "無",
                                "features": features,
                                "limits": {
                                    "twse": twse_stats.to_dict(),
                                    "tpex": tpex_stats.to_dict(),
                                    "combined": combined.to_dict(),
                                },
                                "options_pressure_zones": zones,
                                "source_status": [status.to_dict() for status in statuses],
                            }
                            upsert_history(self.history_path, row, max_rows=10000)
                            written += 1
                except Exception as error:
                    if cursor >= start:
                        skipped += 1
                        print(f"backfill skip date={cursor.isoformat()} error={error}")
                time.sleep(self.delay)
            cursor += timedelta(days=1)
        return written, skipped

    @staticmethod
    def _source_ready(statuses: list[SourceStatus], url: str, target: date) -> bool:
        return any(
            status.url == url
            and status.status == "ready"
            and _parse_date(status.as_of) == target
            for status in statuses
        )

    def _fresh_derivative_features(
        self, target: date, features: dict[str, Any], statuses: list[SourceStatus]
    ) -> dict[str, Any]:
        groups = [
            ("taifex_pc_ratio", {"put_call_volume_ratio", "put_call_oi_ratio", "put_call_sentiment"}),
            ("taiwan_vix", {"taiwan_vix"}),
            ("taifex_daily_futures", {"tx_settlement", "tx_price_source", "futures_basis", "futures_basis_pct"}),
            ("taifex_futures", {
                "foreign_tx_net", "foreign_mtx_net", "foreign_tmf_net", "foreign_futures_net",
                "foreign_tx_long_oi", "foreign_tx_short_oi", "foreign_mtx_long_oi",
                "foreign_mtx_short_oi", "foreign_tmf_long_oi", "foreign_tmf_short_oi",
            }),
        ]
        result: dict[str, Any] = {}
        for source_key, keys in groups:
            if self._source_ready(statuses, self.config.sources[source_key], target):
                result.update({key: features[key] for key in keys if key in features})
        market_oi_ready = any(
            status.name == "TAIFEX市場未平倉量"
            and status.status == "ready"
            and _parse_date(status.as_of) == target
            for status in statuses
        )
        if market_oi_ready:
            for key in ("tx_market_oi", "mtx_market_oi", "tmf_market_oi", "futures_market_oi"):
                if key in features:
                    result[key] = features[key]
        return result

    def _tpex_rows(self, target: date) -> list[dict[str, Any]]:
        url = self.config.sources["tpex_historical_quotes"].format(
            roc_date=roc_date(target),
            gregorian_date=target.strftime("%Y/%m/%d"),
        )
        payload = self.client.get_json(url)
        if isinstance(payload, dict):
            for table in payload.get("tables") or []:
                fields = table.get("fields") or []
                data = table.get("data") or []
                if fields and data and isinstance(data[0], list):
                    return row_objects(fields, data)
            fields = payload.get("fields") or payload.get("columnNames") or []
            data = payload.get("aaData") or payload.get("data") or []
            if fields and data and isinstance(data[0], list):
                return row_objects(fields, data)
            # The legacy historical endpoint often returns only ``aaData``.
            # TPEx documents this 19-column order; keeping the labels here lets
            # the rest of the parser continue to use named official fields.
            if data and isinstance(data[0], list) and len(data[0]) >= len(TPEX_HISTORICAL_FIELDS):
                return row_objects(TPEX_HISTORICAL_FIELDS, data)
            if data and isinstance(data[0], dict):
                return data
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload
        return []

    @staticmethod
    def _value(row: dict[str, Any], *needles: str) -> Any:
        for key, value in row.items():
            text = str(key).replace(" ", "")
            if all(needle in text for needle in needles):
                return value
        return None

    def _tpex_features(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        turnover = [number(self._value(row, "成交金額")) for row in rows]
        clean = [value for value in turnover if value is not None]
        return {"tpex_market_turnover": sum(clean)} if clean else {}

    def _tpex_stats(
        self, rows: list[dict[str, Any]], previous_limits: dict[str, tuple[float, float]]
    ) -> tuple[LimitStats, dict[str, tuple[float, float]]]:
        eligible = up = down = intraday_up = intraday_down = 0
        next_limits: dict[str, tuple[float, float]] = {}
        for row in rows:
            code = str(self._value(row, "代號") or self._value(row, "證券代碼") or "").strip()
            if not COMMON_CODE.match(code):
                continue
            close = number(self._value(row, "收盤"))
            high = number(self._value(row, "最高"))
            low = number(self._value(row, "最低"))
            next_up = number(self._value(row, "次日", "漲停"))
            next_down = number(self._value(row, "次日", "跌停"))
            if next_up is not None and next_down is not None:
                next_limits[code] = (next_up, next_down)
            if close is None or code not in previous_limits:
                continue
            limit_up, limit_down = previous_limits[code]
            eligible += 1
            if abs(close - limit_up) < 1e-8:
                up += 1
            elif high is not None and abs(high - limit_up) < 1e-8:
                intraday_up += 1
            if abs(close - limit_down) < 1e-8:
                down += 1
            elif low is not None and abs(low - limit_down) < 1e-8:
                intraday_down += 1
        stats = LimitStats(
            market="tpex",
            eligible_count=eligible,
            limit_up_count=up,
            limit_down_count=down,
            intraday_up_touch_count=intraday_up,
            intraday_down_touch_count=intraday_down,
            limit_up_ratio=safe_div(up, eligible),
            limit_down_ratio=safe_div(down, eligible),
            limit_breadth=safe_div(up - down, eligible),
            strength_ratio=(up + 1) / (down + 1),
            universe_verified=True,
            calculation_method="official_previous_day_limit_price_crosscheck",
        )
        return stats, next_limits
