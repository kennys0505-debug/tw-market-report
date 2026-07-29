from __future__ import annotations

import math
import re
from html.parser import HTMLParser
from typing import Any


NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def number(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else default
    text = str(value).strip().replace("−", "-").replace("－", "-")
    match = NUMBER_RE.search(text)
    if not match:
        return default
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return default


def percent(value: Any, default: float | None = None) -> float | None:
    result = number(value, default)
    return result / 100.0 if result is not None else default


def row_objects(fields: list[str], data: list[list[Any]]) -> list[dict[str, Any]]:
    return [dict(zip(fields, row)) for row in data]


def find_field(row: dict[str, Any], *needles: str) -> Any:
    for key, value in row.items():
        normalized = str(key).replace(" ", "")
        if all(needle in normalized for needle in needles):
            return value
    return None


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "input" and self._cell is not None:
            # Several TAIFEX tables render their values in readonly form inputs.
            # HTMLParser does not expose those through handle_data(), so preserve
            # the value attribute as cell text.
            value = dict(attrs).get("value")
            if value:
                self._cell.append(str(value))

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def parse_tables(html: str) -> list[list[list[str]]]:
    parser = TableParser()
    parser.feed(html)
    return parser.tables
