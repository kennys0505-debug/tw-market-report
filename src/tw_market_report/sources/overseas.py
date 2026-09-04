from __future__ import annotations

import csv
import io
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..models import SourceStatus
from .http import HttpClient
from .parsing import number


def _returns(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    current = values[-1]
    one = current / values[-2] - 1 if len(values) >= 2 else None
    five = current / values[-6] - 1 if len(values) >= 6 else None
    twenty = current / values[-21] - 1 if len(values) >= 21 else None
    return one, five, twenty


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    return None


def _business_days_late(as_of: date, today: date) -> int:
    days = 0
    cursor = as_of + timedelta(days=1)
    while cursor <= today:
        if cursor.weekday() < 5:
            days += 1
        cursor += timedelta(days=1)
    return days


class OverseasCollector:
    SYMBOLS = {
        "sox": "^SOX",
        "nasdaq": "^IXIC",
        "tsm": "TSM",
        "usd_twd": "TWD=X",
        # Chart enrichment only.  The market state still uses the official
        # TWSE close collected by DomesticCollector.
        "taiex": "^TWII",
    }

    def __init__(self, sources: dict[str, str], client: HttpClient | None = None) -> None:
        self.sources = sources
        self.client = client or HttpClient()

    def collect(self, tsmc_close: float | None) -> tuple[dict[str, Any], list[SourceStatus]]:
        features: dict[str, Any] = {}
        statuses: list[SourceStatus] = []
        self._vix(features, statuses)
        series: dict[str, tuple[list[float], str | None]] = {}
        for name, symbol in self.SYMBOLS.items():
            try:
                price_rows, as_of = self._yahoo_prices(symbol)
                closes = [float(row["close"]) for row in price_rows]
                series[name] = (closes, as_of)
                if name == "taiex":
                    features["taiex_price_history"] = price_rows
                statuses.append(SourceStatus(f"Yahoo {symbol}", "ready", as_of, url=self._yahoo_url(symbol)))
            except Exception as error:
                statuses.append(SourceStatus(f"Yahoo {symbol}", "partial", message=str(error), url=self._yahoo_url(symbol)))
        if "sox" in series:
            one, five, twenty = _returns(series["sox"][0])
            features.update({"sox_return_1d": one, "sox_return_5d": five, "sox_return_20d": twenty})
        if "nasdaq" in series and "sox" in series:
            _, sox5, _ = _returns(series["sox"][0])
            _, nasdaq5, _ = _returns(series["nasdaq"][0])
            if sox5 is not None and nasdaq5 is not None:
                features["sox_relative_nasdaq"] = sox5 - nasdaq5
        if "usd_twd" in series:
            _, fx5, _ = _returns(series["usd_twd"][0])
            features["usd_twd"] = series["usd_twd"][0][-1]
            features["usd_twd_change_5d"] = fx5
        if "tsm" in series:
            features["tsm_adr_close"] = series["tsm"][0][-1]
        if tsmc_close and features.get("tsm_adr_close") and features.get("usd_twd"):
            equivalent = float(features["tsm_adr_close"]) * float(features["usd_twd"]) / 5.0
            features["tsm_adr_equivalent_twd"] = equivalent
            features["tsm_adr_premium"] = equivalent / tsmc_close - 1.0
        today = datetime.now(timezone.utc).date()
        stale = [status for status in statuses if status.status == "ready" and (parsed := _parse_date(status.as_of)) and _business_days_late(parsed, today) > 1]
        if stale:
            for status in stale:
                status.status = "stale"
                status.message = "free end-of-day data is more than one business day old"
            features["overseas_data_stale"] = True
        return features, statuses

    def _vix(self, features: dict, statuses: list[SourceStatus]) -> None:
        url = self.sources["cboe_vix_csv"]
        try:
            rows = list(csv.DictReader(io.StringIO(self.client.get_text(url))))
            closes = [number(row.get("CLOSE")) for row in rows]
            clean = [value for value in closes if value is not None]
            if not clean:
                raise ValueError("Cboe VIX CSV was empty")
            one, five, _ = _returns(clean)
            features.update({"us_vix": clean[-1], "us_vix_change_1d": one, "us_vix_change_5d": five})
            raw_date = rows[-1].get("DATE")
            parsed_date = _parse_date(raw_date)
            statuses.append(SourceStatus("Cboe VIX", "ready", parsed_date.isoformat() if parsed_date else raw_date, url=url))
        except Exception as error:
            statuses.append(SourceStatus("Cboe VIX", "partial", message=str(error), url=url))

    def _yahoo_url(self, symbol: str) -> str:
        return self.sources["yahoo_chart"].format(symbol=urllib.parse.quote(symbol, safe=""))

    def _yahoo_prices(self, symbol: str) -> tuple[list[dict[str, Any]], str | None]:
        payload = self.client.get_json(self._yahoo_url(symbol))
        result = payload["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        timestamps = result.get("timestamp", [])
        rows: list[dict[str, Any]] = []
        for index, stamp in enumerate(timestamps):
            close = (quote.get("close") or [])[index] if index < len(quote.get("close") or []) else None
            if close is None:
                continue
            row = {
                "date": datetime.fromtimestamp(stamp, timezone.utc).date().isoformat(),
                "close": float(close),
            }
            for key in ("open", "high", "low"):
                values = quote.get(key) or []
                value = values[index] if index < len(values) else None
                row[key] = float(value) if value is not None else float(close)
            rows.append(row)
        as_of = None
        if rows:
            as_of = rows[-1]["date"]
        if not rows:
            raise ValueError(f"No close data for {symbol}")
        return rows, as_of

    def _yahoo(self, symbol: str) -> tuple[list[float], str | None]:
        rows, as_of = self._yahoo_prices(symbol)
        return [float(row["close"]) for row in rows], as_of
