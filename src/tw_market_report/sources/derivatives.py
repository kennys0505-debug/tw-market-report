from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..models import SourceStatus
from ..stats import safe_div
from .http import HttpClient
from .parsing import number, parse_tables


def _date_text(value: str) -> str:
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed else value.replace("/", "-")[:10]


def _parse_date(value: str) -> date | None:
    tokens = value.strip().split()
    if not tokens:
        return None
    text = tokens[0]
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    parts = text.replace("-", "/").split("/")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        year, month, day = map(int, parts)
        if year < 1911:
            year += 1911
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _latest(rows: list[list[str]]) -> list[str]:
    dated = [(parsed, row) for row in rows if row and (parsed := _parse_date(row[0]))]
    if not dated:
        raise ValueError("no valid dated row found")
    return max(dated, key=lambda item: item[0])[1]


def _page_date(tables: list[list[list[str]]], fallback: date) -> date:
    parsed = [
        candidate
        for table in tables
        for row in table
        for cell in row
        if (candidate := _parse_date(cell)) is not None
    ]
    return max(parsed) if parsed else fallback


class DerivativesCollector:
    def __init__(self, sources: dict[str, str], client: HttpClient | None = None) -> None:
        self.sources = sources
        self.client = client or HttpClient()

    def collect(self, trade_date: date, spot: float | None) -> tuple[dict[str, Any], dict[str, Any], list[SourceStatus]]:
        features: dict[str, Any] = {}
        zones: dict[str, Any] = {"calls": [], "puts": [], "max_pain": None, "status": "partial"}
        statuses: list[SourceStatus] = []
        self._pc_ratio(features, statuses, trade_date)
        self._taiwan_vix(features, statuses, trade_date)
        self._futures(features, statuses, spot, trade_date)
        self._institution_positions(features, statuses, trade_date)
        self._options(zones, statuses, spot, trade_date)
        return features, zones, statuses

    def _pc_ratio(self, features: dict, statuses: list[SourceStatus], trade_date: date) -> None:
        url = self.sources["taifex_pc_ratio"]
        try:
            tables = parse_tables(self.client.get_text(url))
            candidates = []
            for table in tables:
                for row in table:
                    if len(row) >= 7 and "/" in row[0] and number(row[3]) is not None:
                        candidates.append(row)
            if not candidates:
                raise ValueError("Put/Call table not found")
            row = _latest(candidates)
            volume_ratio = number(row[3])
            oi_ratio = number(row[6])
            features["put_call_volume_ratio"] = (volume_ratio or 100) / 100.0
            features["put_call_oi_ratio"] = (oi_ratio or 100) / 100.0
            # Centered interpretation; extremes remain diagnostic rather than directional.
            features["put_call_sentiment"] = 1.0 - min(abs(features["put_call_oi_ratio"] - 1.0), 1.0)
            as_of = _parse_date(row[0])
            fresh = as_of == trade_date
            message = "" if fresh else f"最新資料為 {as_of.isoformat() if as_of else row[0]}，非報告交易日"
            statuses.append(SourceStatus("TAIFEX Put/Call", "ready" if fresh else "partial", _date_text(row[0]), message, url))
        except Exception as error:
            statuses.append(SourceStatus("TAIFEX Put/Call", "partial", message=str(error), url=url))

    def _taiwan_vix(self, features: dict, statuses: list[SourceStatus], trade_date: date) -> None:
        page_url = self.sources["taiwan_vix"]
        url = self.sources.get(
            "taiwan_vix_data",
            "https://www.taifex.com.tw/cht/7/getVixData?filesname={date}",
        ).format(date=trade_date.strftime("%Y%m%d"))
        try:
            text = self.client.get_text(url)
            candidates: list[float] = []
            for line in text.splitlines():
                cells = [cell for cell in re.split(r"[\s,\t]+", line.strip()) if cell]
                if len(cells) < 2:
                    continue
                value = number(cells[-1])
                if value is not None and 0 < value < 200:
                    candidates.append(value)
            if not candidates:
                raise ValueError("TAIWAN VIX value not found")
            features["taiwan_vix"] = candidates[-1]
            statuses.append(SourceStatus("TAIWAN VIX", "ready", trade_date.isoformat(), url=page_url))
        except Exception as error:
            statuses.append(SourceStatus("TAIWAN VIX", "partial", trade_date.isoformat(), str(error), page_url))

    def _futures(self, features: dict, statuses: list[SourceStatus], spot: float | None, trade_date: date) -> None:
        url = self.sources["taifex_daily_futures"]
        try:
            query_date = trade_date.strftime("%Y/%m/%d")
            html = self.client.post_form_text(url, {
                "queryType": "2", "marketCode": "0", "MarketCode": "0",
                "dateaddcnt": "", "commodity_id": "TX", "commodity_idt": "TX",
                "commodity_id2": "", "commodity_id2t": "", "queryDate": query_date,
            })
            tables = parse_tables(html)
            candidates: list[dict[str, Any]] = []
            for table in tables:
                for row in table:
                    if len(row) >= 8 and row[0].strip() in {"TX", "臺股期貨"}:
                        nums = [number(cell) for cell in row]
                        open_interest = number(row[12]) if len(row) > 12 else None
                        candidates.append({"row": row, "numbers": nums, "open_interest": open_interest})
            if not candidates:
                raise ValueError("TX daily row not found")
            # Prefer the contract carrying the most open interest.  This avoids
            # selecting an illiquid deferred row merely because it appears first.
            selected = max(candidates, key=lambda item: float(item.get("open_interest") or 0.0))
            row = selected["row"]
            settlement = None
            price_source = "settlement"
            for candidate in sorted(candidates, key=lambda item: float(item.get("open_interest") or 0.0), reverse=True):
                candidate_row = candidate["row"]
                value = number(candidate_row[11]) if len(candidate_row) > 12 else number(candidate_row[9]) if len(candidate_row) > 9 else None
                plausible = value is not None and (not spot or 0.7 * spot <= value <= 1.3 * spot)
                if plausible:
                    row, settlement = candidate_row, value
                    break
            # Some responses put the after-hours table first and do not publish
            # a settlement value there.  A last-trade fallback is allowed only
            # when it is close to spot and is explicitly labelled.
            if settlement is None:
                for candidate in candidates:
                    candidate_row = candidate["row"]
                    value = number(candidate_row[5]) if len(candidate_row) > 5 else None
                    plausible = value is not None and (not spot or 0.7 * spot <= value <= 1.3 * spot)
                    if plausible:
                        row, settlement, price_source = candidate_row, value, "last_trade_fallback"
                        break
            if settlement is None:
                raise ValueError("TX settlement/last price failed plausibility validation")
            features["tx_settlement"] = settlement
            features["tx_price_source"] = price_source
            features["tx_selected_contract"] = row[1] if len(row) > 1 else None
            if spot:
                features["futures_basis"] = settlement - spot
                features["futures_basis_pct"] = (settlement - spot) / spot
            as_of = _page_date(tables, trade_date)
            statuses.append(SourceStatus("TAIFEX台指期行情", "ready", as_of.isoformat(), url=url))
        except Exception as error:
            statuses.append(SourceStatus("TAIFEX台指期行情", "partial", message=str(error), url=url))
        self._market_open_interest(features, statuses, trade_date)

    def _market_open_interest(
        self, features: dict, statuses: list[SourceStatus], trade_date: date
    ) -> None:
        """Collect true market OI for TX/MTX/TMF from the daily market tables."""
        url = self.sources.get("taifex_daily_futures")
        if not url:
            statuses.append(SourceStatus("TAIFEX市場未平倉量", "partial", message="未設定每日行情明細來源"))
            return
        weights = {"TX": 1.0, "MTX": 0.25, "TMF": 0.05}
        totals: dict[str, float] = {}
        missing: list[str] = []
        for product in weights:
            try:
                html = self.client.post_form_text(url, {
                    "queryType": "2", "marketCode": "0", "MarketCode": "0",
                    "dateaddcnt": "", "commodity_id": product, "commodity_idt": product,
                    "commodity_id2": "", "commodity_id2t": "",
                    "queryDate": trade_date.strftime("%Y/%m/%d"),
                })
                tables = parse_tables(html)
                page_dates = {
                    parsed
                    for table in tables
                    for row in table
                    for cell in row
                    if (parsed := _parse_date(cell)) is not None
                }
                if page_dates and max(page_dates) != trade_date:
                    raise ValueError(f"returned {max(page_dates).isoformat()}, expected {trade_date.isoformat()}")
                rows = [
                    row for table in tables for row in table
                    if len(row) > 12 and row[0].strip() == product
                ]
                values = [number(row[12]) for row in rows]
                total = sum(float(value) for value in values if value is not None and value >= 0)
                if total <= 0:
                    raise ValueError("open-interest rows not found")
                totals[product] = total
                features[f"{product.lower()}_market_oi"] = total
            except Exception:
                missing.append(product)
        if len(totals) == len(weights):
            features["futures_market_oi"] = sum(totals[product] * weights[product] for product in weights)
            statuses.append(
                SourceStatus(
                    "TAIFEX市場未平倉量", "ready", trade_date.isoformat(),
                    url=url,
                )
            )
        else:
            statuses.append(
                SourceStatus(
                    "TAIFEX市場未平倉量", "partial", trade_date.isoformat(),
                    f"缺少商品：{'、'.join(missing)}",
                )
            )

    def _institution_positions(self, features: dict, statuses: list[SourceStatus], trade_date: date) -> None:
        url = self.sources["taifex_futures"]
        try:
            html = self.client.post_form_text(url, {
                "queryType": "1", "goDay": "", "doQuery": "1", "dateaddcnt": "",
                "queryDate": trade_date.strftime("%Y/%m/%d"), "commodityId": "",
            })
            tables = parse_tables(html)
            text_rows = [row for table in tables for row in table]
            foreign_nets: list[float] = []
            current_product = ""
            weights = {"臺股期貨": 1.0, "小型臺指期貨": 0.25, "微型臺指期貨": 0.05}
            product_codes = {"臺股期貨": "tx", "小型臺指期貨": "mtx", "微型臺指期貨": "tmf"}
            for row in text_rows:
                joined = " ".join(row)
                for product in weights:
                    if product in joined:
                        current_product = product
                        break
                identity = next((cell.strip() for cell in row if cell.strip() in {"自營商", "投信", "外資"}), "")
                if current_product and identity == "外資":
                    nums = [number(cell) for cell in row]
                    clean = [value for value in nums if value is not None]
                    if len(clean) >= 2:
                        net_contracts = clean[-2]
                        foreign_nets.append(net_contracts * weights[current_product])
                        code = product_codes[current_product]
                        features[f"foreign_{code}_net"] = net_contracts
                        if len(clean) >= 12:
                            features[f"foreign_{code}_long_oi"] = clean[-6]
                            features[f"foreign_{code}_short_oi"] = clean[-4]
            if foreign_nets:
                features["foreign_futures_net"] = sum(foreign_nets)
            message = "" if foreign_nets else "回傳成功，但找不到臺指期外資未平倉淨額"
            as_of = _page_date(tables, trade_date)
            statuses.append(SourceStatus("TAIFEX三大法人期貨", "ready" if foreign_nets else "partial", as_of.isoformat(), message, url))
        except Exception as error:
            statuses.append(SourceStatus("TAIFEX三大法人期貨", "partial", message=str(error), url=url))

    def _options(self, zones: dict, statuses: list[SourceStatus], spot: float | None, trade_date: date) -> None:
        url = self.sources["taifex_options"]
        try:
            query_date = trade_date.strftime("%Y/%m/%d")
            html = self.client.post_form_text(url, {
                "queryType": "2", "marketCode": "0", "MarketCode": "0",
                "dateaddcnt": "", "commodity_id": "TXO", "commodity_idt": "TXO",
                "commodity_id2": "", "commodity_id2t": "", "queryDate": query_date,
            })
            tables = parse_tables(html)
            records: list[dict[str, Any]] = []
            side = None
            for table in tables:
                for row in table:
                    joined = " ".join(row)
                    if "買權" in joined and len(row) <= 3:
                        side = "call"
                        continue
                    if "賣權" in joined and len(row) <= 3:
                        side = "put"
                        continue
                    if side and len(row) >= 8:
                        strike = number(row[2])
                        oi = number(row[-1])
                        if strike and oi is not None and (not spot or abs(strike / spot - 1) <= 0.10):
                            records.append({"side": side, "strike": strike, "open_interest": oi, "weight": oi})
            if not records:
                raise ValueError("option strike/open-interest rows not found")
            for side, target in (("call", "calls"), ("put", "puts")):
                ranked = sorted((r for r in records if r["side"] == side), key=lambda r: r["weight"], reverse=True)[:3]
                zones[target] = ranked
            strikes = sorted({r["strike"] for r in records})
            if strikes:
                payouts = []
                for settlement in strikes:
                    payout = sum(
                        max(0.0, settlement - r["strike"]) * r["open_interest"] if r["side"] == "call"
                        else max(0.0, r["strike"] - settlement) * r["open_interest"]
                        for r in records
                    )
                    payouts.append((payout, settlement))
                zones["max_pain"] = min(payouts)[1]
            zones["status"] = "ready"
            if zones["calls"] and zones["puts"] and spot:
                call_weight = sum(r["weight"] for r in zones["calls"])
                put_weight = sum(r["weight"] for r in zones["puts"])
                zones["pressure_balance"] = safe_div(put_weight - call_weight, put_weight + call_weight)
            as_of = _page_date(tables, trade_date)
            statuses.append(SourceStatus("TAIFEX選擇權履約價", "ready", as_of.isoformat(), url=url))
        except Exception as error:
            statuses.append(SourceStatus("TAIFEX選擇權履約價", "partial", message=str(error), url=url))
