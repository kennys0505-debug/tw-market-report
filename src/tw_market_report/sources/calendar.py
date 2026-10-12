from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re
from typing import Any

from .http import HttpClient, SourceError


CLOSED_MARKERS = ("市場無交易", "休市", "放假", "補假", "補休", "停止交易")
OPEN_MARKERS = ("開始交易", "最後交易日", "恢復交易")


class CalendarUnavailableError(SourceError):
    """A strict publication check could not verify the official calendar."""


def _normalize_date(value: Any) -> str:
    text = str(value).strip()
    if re.fullmatch(r"\d{7,8}", text):
        year, month, day = int(text[:-4]), int(text[-4:-2]), int(text[-2:])
    else:
        match = re.fullmatch(r"(\d{2,4})[-/](\d{1,2})[-/](\d{1,2})", text)
        if not match:
            return ""
        year, month, day = map(int, match.groups())
    if 1 <= year <= 300:
        year += 1911
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _find_date(row: dict[str, Any]) -> str:
    for key, value in row.items():
        if "日期" in str(key) or str(key).lower() in {"date", "holidaydate"}:
            return _normalize_date(value)
    return ""


def is_taiwan_trading_day(target: date, sources: dict[str, str], client: HttpClient | None = None,
                          strict: bool = False) -> bool:
    if target.weekday() >= 5:
        return False
    client = client or HttpClient(timeout=12, retries=1)
    try:
        url = sources["twse_calendar"].format(roc_year=target.year - 1911)
        payload = client.get_json(url)
        rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or (strict and not rows):
            raise ValueError("Official calendar response has no usable rows")
        normalized = []
        for row in rows:
            day = _find_date(row) if isinstance(row, dict) else ""
            if not day:
                raise ValueError("Official calendar contains an unrecognized date or row")
            normalized.append((day, row))
        if strict and not any(day.startswith(f"{target.year}-") for day, _ in normalized):
            raise ValueError("Official calendar does not cover the requested year")
        target_text = target.isoformat()
        for day, row in normalized:
            if day != target_text:
                continue
            text = " ".join(str(value) for value in row.values())
            if any(marker in text for marker in OPEN_MARKERS):
                return True
            if any(marker in text for marker in CLOSED_MARKERS):
                return False
            if strict:
                raise ValueError("Official calendar entry has an unknown market status")
        return True
    except Exception as error:
        if strict:
            raise CalendarUnavailableError(f"Cannot verify trading calendar: {error}") from error
        # Source failure must not suppress a legitimate trading day. The close pipeline
        # still refuses to publish a new state when its official core data is absent.
        return True


def latest_completed_trading_day(
    now: datetime,
    sources: dict[str, str],
    client: HttpClient | None = None,
    close_cutoff: time = time(15, 0),
) -> date:
    """Resolve the latest session whose official close should already exist."""
    candidate = now.date() if now.time() >= close_cutoff else now.date() - timedelta(days=1)
    for _ in range(14):
        if is_taiwan_trading_day(candidate, sources, client):
            return candidate
        candidate -= timedelta(days=1)
    raise RuntimeError("Unable to resolve a completed Taiwan trading day")
