import unittest

from tw_market_report.backfill import HistoryBackfiller, roc_date


class BackfillTests(unittest.TestCase):
    def test_roc_date(self):
        from datetime import date
        self.assertEqual(roc_date(date(2026, 7, 28)), "115/07/28")

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


if __name__ == "__main__":
    unittest.main()
