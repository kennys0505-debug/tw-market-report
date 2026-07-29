from __future__ import annotations

from datetime import date
from typing import Any

from .http import HttpClient


CLOSED_MARKERS = ("市場無交易", "休市", "放假", "停止交易")
OPEN_MARKERS = ("開始交易", "最後交易日", "恢復交易")


def _find_date(row: dict[str, Any]) -> str:
    for key, value in row.items():
        if "日期" in str(key) or str(key).lower() in {"date", "holidaydate"}:
            return str(value).replace("/", "-")
    return ""


def is_taiwan_trading_day(target: date, sources: dict[str, str], client: HttpClient | None = None) -> bool:
    if target.weekday() >= 5:
        return False
    client = client or HttpClient(timeout=12, retries=1)
    url = sources["twse_calendar"].format(roc_year=target.year - 1911)
    try:
        payload = client.get_json(url)
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        target_text = target.isoformat()
        for row in rows:
            if not isinstance(row, dict) or _find_date(row)[:10] != target_text:
                continue
            text = " ".join(str(value) for value in row.values())
            if any(marker in text for marker in OPEN_MARKERS):
                return True
            if any(marker in text for marker in CLOSED_MARKERS):
                return False
        return True
    except Exception:
        # Source failure must not suppress a legitimate trading day. The close pipeline
        # still refuses to publish a new state when its official core data is absent.
        return True
