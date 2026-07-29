from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .history import load_json, write_json


def line_message(payload: dict[str, Any], report_url: str = "") -> str:
    mode = "收盤" if payload.get("report_mode") == "close" else "盤前"
    lines = [
        f"【台股{mode}報告】{payload.get('trade_date')}",
        f"狀態：{payload.get('domestic_market_state')}｜分數 {payload.get('composite_score')}｜信心 {payload.get('confidence')}",
        f"模型曝險：{'–'.join(map(str, payload.get('model_exposure_range', [])))}%" + ("（影子）" if payload.get("shadow_mode") else ""),
        f"漲停 {payload.get('limit_up_count', 0)} 家（5年 {payload.get('limit_up_percentile_5y') or '—'} 分位）／跌停 {payload.get('limit_down_count', 0)} 家（5年 {payload.get('limit_down_percentile_5y') or '—'} 分位）",
        f"反轉階段：{payload.get('reversal_stage', '無')}",
    ]
    if payload.get("report_mode") == "premarket":
        lines.append(f"海外風險：{payload.get('overnight_risk_state', '中性')}")
    drivers = (payload.get("positive_drivers") or [])[:1] + (payload.get("negative_drivers") or [])[:1]
    if drivers:
        lines.append("主因：" + "；".join(drivers))
    if report_url:
        lines.append(report_url.rstrip("/") + "/")
    return "\n".join(lines)[:5000]


def send_line(payload: dict[str, Any], digest: str, state_path: Path, report_url: str = "") -> bool:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token or not user_id:
        return False
    state = load_json(state_path, {}) or {}
    key = f"{payload.get('trade_date')}:{payload.get('report_mode')}"
    if state.get(key) == digest:
        return False
    body = json.dumps({"to": user_id, "messages": [{"type": "text", "text": line_message(payload, report_url)}]}).encode()
    request = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-Line-Retry-Key": str(uuid.UUID(digest[:32]))},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status >= 300:
                raise RuntimeError(f"LINE returned {response.status}")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"LINE push failed: {error.code} {error.read().decode(errors='replace')}") from error
    state[key] = digest
    write_json(state_path, state)
    return True
