from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import ReportConfig
from .history import load_history, upsert_history
from .limits import combine_limit_stats
from .models import LimitStats
from .sources.calendar import is_taiwan_trading_day
from .sources.domestic import DomesticCollector
from .sources.http import HttpClient
from .sources.parsing import number, row_objects
from .stats import safe_div


COMMON_CODE = re.compile(r"^[1-9]\d{3}$")


def roc_date(value: date) -> str:
    return f"{value.year - 1911}/{value.month:02d}/{value.day:02d}"


class HistoryBackfiller:
    def __init__(self, config: ReportConfig, delay: float = 0.35) -> None:
        self.config = config
        self.client = HttpClient(timeout=25, retries=2)
        self.domestic = DomesticCollector(config.sources, self.client)
        self.delay = delay
        self.history_path = config.root / "data" / "history.jsonl"

    def run(self, start: date, end: date) -> tuple[int, int]:
        if end < start:
            raise ValueError("end must be on or after start")
        # Warm up the previous-day limit-price map without persisting warm-up days.
        cursor = start - timedelta(days=14)
        previous_limits: dict[str, tuple[float, float]] = {}
        written = skipped = 0
        while cursor <= end:
            if cursor.weekday() < 5 and is_taiwan_trading_day(cursor, self.config.sources, self.client):
                try:
                    current_rows = self._tpex_rows(cursor)
                    tpex_stats, next_limits = self._tpex_stats(current_rows, previous_limits)
                    previous_limits = next_limits or previous_limits
                    if cursor >= start:
                        twse_features, twse_stats, status = self.domestic.collect_twse_only(cursor)
                        if status.status != "ready" or twse_stats is None or twse_features.get("taiex_close") is None:
                            skipped += 1
                        elif tpex_stats.eligible_count == 0:
                            skipped += 1
                        else:
                            combined = combine_limit_stats([twse_stats, tpex_stats])
                            row = {
                                "trade_date": cursor.isoformat(),
                                "report_mode": "close",
                                "limit_scope": "combined",
                                "taiex_close": twse_features["taiex_close"],
                                "limit_up_count": combined.limit_up_count,
                                "limit_down_count": combined.limit_down_count,
                                "limit_up_ratio": combined.limit_up_ratio,
                                "limit_down_ratio": combined.limit_down_ratio,
                                "composite_score": 50.0,
                                "domestic_market_state": "盤整",
                                "reversal_stage": "無",
                                "features": {"taiex_close": twse_features["taiex_close"], "limit_breadth": combined.limit_breadth},
                                "limits": {
                                    "twse": twse_stats.to_dict(),
                                    "tpex": tpex_stats.to_dict(),
                                    "combined": combined.to_dict(),
                                },
                            }
                            upsert_history(self.history_path, row, max_rows=10000)
                            written += 1
                except Exception:
                    if cursor >= start:
                        skipped += 1
                time.sleep(self.delay)
            cursor += timedelta(days=1)
        return written, skipped

    def _tpex_rows(self, target: date) -> list[dict[str, Any]]:
        url = self.config.sources["tpex_historical_quotes"].format(roc_date=roc_date(target))
        payload = self.client.get_json(url)
        if isinstance(payload, dict):
            fields = payload.get("fields") or payload.get("columnNames") or []
            data = payload.get("aaData") or payload.get("data") or []
            if fields and data and isinstance(data[0], list):
                return row_objects(fields, data)
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
