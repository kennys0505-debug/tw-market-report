from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    temporary.replace(path)


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return sorted(rows, key=lambda row: (row.get("trade_date", ""), row.get("report_mode", "")))


def upsert_history(path: Path, row: dict[str, Any], max_rows: int = 10000) -> list[dict[str, Any]]:
    rows = load_history(path)
    key = (row.get("trade_date"), row.get("report_mode"))
    rows = [item for item in rows if (item.get("trade_date"), item.get("report_mode")) != key]
    rows.append(row)
    rows = sorted(rows, key=lambda item: (item.get("trade_date", ""), item.get("report_mode", "")))[-max_rows:]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for item in rows:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)
    return rows


def merge_history_files(paths: list[Path], output: Path, max_rows: int = 10000) -> list[dict[str, Any]]:
    """Merge independently backfilled chunks using date/mode as the stable key."""
    merged: dict[tuple[Any, Any], dict[str, Any]] = {}
    candidates = ([output] if output.exists() else []) + paths
    for path in candidates:
        for row in load_history(path):
            key = (row.get("trade_date"), row.get("report_mode"))
            if key[0]:
                merged[key] = row
    rows = sorted(merged.values(), key=lambda row: (row.get("trade_date", ""), row.get("report_mode", "")))[-max_rows:]
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(output)
    return rows
