import unittest

from tw_market_report.limits import calculate_limit_stats, combine_limit_stats, historical_analogs


class LimitStatsTests(unittest.TestCase):
    def test_eligible_universe_and_intraday_touches(self):
        rows = [
            {"code": "1101", "security_type": "COMMON", "close": 55, "high": 55, "low": 50, "limit_up_price": 55, "limit_down_price": 45},
            {"code": "1102", "security_type": "COMMON", "close": 48, "high": 55, "low": 47, "limit_up_price": 55, "limit_down_price": 45},
            {"code": "1103", "security_type": "COMMON", "close": 45, "high": 50, "low": 45, "limit_up_price": 55, "limit_down_price": 45},
            {"code": "0050", "security_type": "ETF", "close": 100, "high": 100, "low": 100, "limit_up_price": 100, "limit_down_price": 80},
            {"code": "9999", "security_type": "COMMON", "close": 50, "tradable": False, "limit_up_price": 55, "limit_down_price": 45},
        ]
        stats = calculate_limit_stats(rows, "twse")
        self.assertEqual(stats.eligible_count, 3)
        self.assertEqual(stats.limit_up_count, 1)
        self.assertEqual(stats.limit_down_count, 1)
        self.assertEqual(stats.intraday_up_touch_count, 1)
        self.assertAlmostEqual(stats.limit_breadth, 0)

    def test_combined_uses_ratio_denominator(self):
        first = calculate_limit_stats([
            {"close": 11, "high": 11, "low": 10, "limit_up_price": 11, "limit_down_price": 9}
        ], "twse")
        second = calculate_limit_stats([
            {"close": 9, "high": 10, "low": 9, "limit_up_price": 11, "limit_down_price": 9},
            {"close": 10, "high": 10, "low": 10, "limit_up_price": 11, "limit_down_price": 9},
        ], "tpex")
        combined = combine_limit_stats([first, second])
        self.assertEqual(combined.eligible_count, 3)
        self.assertAlmostEqual(combined.limit_up_ratio, 1 / 3)
        self.assertAlmostEqual(combined.limit_down_ratio, 1 / 3)

    def test_analogs_include_forward_returns_without_lookahead_at_end(self):
        history = [
            {"trade_date": f"2026-01-{i+1:02d}", "limit_up_ratio": i / 1000, "limit_down_ratio": i / 2000, "taiex_close": 100 + i}
            for i in range(25)
        ]
        current = combine_limit_stats([])
        current.limit_up_ratio = 0.005
        current.limit_down_ratio = 0.0025
        analogs, summary = historical_analogs(current, history, limit=3)
        self.assertEqual(len(analogs), 3)
        self.assertIn("10d", summary)
        self.assertGreaterEqual(summary["1d"]["sample_count"], 1)


if __name__ == "__main__":
    unittest.main()

