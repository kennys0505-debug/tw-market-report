import tempfile
import unittest
from datetime import date
from pathlib import Path

from tw_market_report.backfill import (
    TPEX_HISTORICAL_FIELDS,
    HistoricalOverseasCache,
    HistoryBackfiller,
    roc_date,
)
from tw_market_report.history import load_history, merge_history_files, upsert_history


class BackfillTests(unittest.TestCase):
    def test_roc_date(self):
        self.assertEqual(roc_date(date(2026, 7, 28)), "115/07/28")

    def test_tpex_legacy_aadata_without_fields(self):
        row = [
            "6488", "環球晶", "400", "+1", "399", "405", "398", "401",
            "1000", "400000", "50", "399", "1", "400", "1", "1000000",
            "400", "440", "360",
        ]

        class Client:
            def get_json(self, _url):
                return {"aaData": [row]}

        backfiller = object.__new__(HistoryBackfiller)
        backfiller.client = Client()
        backfiller.config = type(
            "Config", (), {"sources": {"tpex_historical_quotes": "https://example.test?d={roc_date}"}}
        )()
        rows = backfiller._tpex_rows(date(2020, 1, 2))
        self.assertEqual(list(rows[0]), TPEX_HISTORICAL_FIELDS)
        self.assertEqual(rows[0]["次日漲停價"], "440")

    def test_tpex_stats_uses_previous_official_limits(self):
        filler = object.__new__(HistoryBackfiller)
        rows = [
            {"代號": "1234", "收盤": "55", "最高": "55", "最低": "50", "次日漲停價": "60.5", "次日跌停價": "49.5"},
            {"代號": "5678", "收盤": "45", "最高": "50", "最低": "45", "次日漲停價": "49.5", "次日跌停價": "40.5"},
            {"代號": "0050", "收盤": "100", "最高": "100", "最低": "100", "次日漲停價": "110", "次日跌停價": "90"},
        ]
        stats, next_limits = filler._tpex_stats(rows, {"1234": (55, 45), "5678": (55, 45)})
        self.assertEqual(stats.eligible_count, 2)
        self.assertEqual(stats.limit_up_count, 1)
        self.assertEqual(stats.limit_down_count, 1)
        self.assertIn("1234", next_limits)

    def test_overseas_alignment_does_not_look_into_same_day_us_session(self):
        cache = object.__new__(HistoricalOverseasCache)
        cache.series = {
            "vix": [(date(2026, 8, 4), 18.0), (date(2026, 8, 5), 22.0)],
            "sox": [(date(2026, 8, 4), 100.0), (date(2026, 8, 5), 110.0)],
            "nasdaq": [(date(2026, 8, 4), 100.0), (date(2026, 8, 5), 105.0)],
            "tsm": [(date(2026, 8, 4), 250.0), (date(2026, 8, 5), 260.0)],
            "usd_twd": [(date(2026, 8, 4), 30.0), (date(2026, 8, 5), 30.5)],
        }
        features = cache.features_for(date(2026, 8, 5), 1525.0)
        self.assertEqual(features["us_vix"], 18.0)
        self.assertEqual(features["tsm_adr_close"], 250.0)
        self.assertEqual(features["usd_twd"], 30.5)
        self.assertAlmostEqual(features["tsm_adr_premium"], 0.0)

    def test_tpex_turnover_is_aggregated(self):
        filler = object.__new__(HistoryBackfiller)
        features = filler._tpex_features([
            {"成交金額(元)": "1,200"},
            {"成交金額(元)": "800"},
        ])
        self.assertEqual(features["tpex_market_turnover"], 2000)

    def test_merge_history_chunks_uses_trade_date_and_mode_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, output = root / "first.jsonl", root / "second.jsonl", root / "history.jsonl"
            upsert_history(first, {"trade_date": "2026-08-04", "report_mode": "close", "value": 1})
            upsert_history(second, {"trade_date": "2026-08-04", "report_mode": "close", "value": 2})
            upsert_history(second, {"trade_date": "2026-08-05", "report_mode": "close", "value": 3})
            rows = merge_history_files([first, second], output)
            self.assertEqual(len(rows), 2)
            self.assertEqual(load_history(output)[0]["value"], 2)


if __name__ == "__main__":
    unittest.main()
