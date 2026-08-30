import unittest

from tw_market_report.technical import auxiliary_adjustment, exposure_for_score, technical_analysis


def trend_history(up: bool = True, sessions: int = 180):
    rows = []
    taiex = 18000.0
    otc = 220.0
    step = 0.002 if up else -0.002
    for index in range(sessions):
        taiex *= 1.0 + step
        otc *= 1.0 + step * 1.1
        rows.append({
            "trade_date": f"2026-01-{index + 1:02d}",
            "taiex_close": taiex,
            "features": {
                "otc_close": otc,
                "market_turnover": 300_000_000_000 * (1.0 + index / 1000),
                "otc_turnover": 80_000_000_000 * (1.0 + index / 1000),
            },
        })
    return rows


class TechnicalAnalysisTests(unittest.TestCase):
    def test_two_indices_confirm_strong_uptrend(self):
        history = trend_history(True)
        current = {
            "taiex_close": history[-1]["taiex_close"] * 1.004,
            "otc_close": history[-1]["features"]["otc_close"] * 1.004,
            "market_turnover": 390_000_000_000,
            "otc_turnover": 105_000_000_000,
        }
        result = technical_analysis(current, history)
        self.assertGreater(result["score"], 65)
        self.assertIn(result["state"], {"轉多", "強多"})
        self.assertEqual(result["synchrony"], "同向確認")
        self.assertIsNotNone(result["taiex"]["confirmation_level"])

    def test_two_indices_confirm_downtrend(self):
        history = trend_history(False)
        current = {
            "taiex_close": history[-1]["taiex_close"] * 0.996,
            "otc_close": history[-1]["features"]["otc_close"] * 0.996,
            "market_turnover": 390_000_000_000,
            "otc_turnover": 105_000_000_000,
        }
        result = technical_analysis(current, history)
        self.assertLess(result["score"], 40)
        self.assertIn(result["state"], {"轉空", "強空"})

    def test_otc_uses_official_history_and_backfill_turnover_alias(self):
        history = trend_history(True, 25)
        for row in history:
            row["features"].pop("otc_close")
            row["features"]["tpex_market_turnover"] = row["features"].pop("otc_turnover")
        otc_history = [
            {"date": f"2026{i + 1:04d}", "close": 220.0 + i}
            for i in range(80)
        ]
        current = {
            "taiex_close": history[-1]["taiex_close"] * 1.002,
            "otc_close": 301.0,
            "otc_turnover": 90_000_000_000,
            "otc_history": otc_history,
        }
        otc = technical_analysis(current, history)["otc"]
        self.assertGreaterEqual(otc["coverage"], 0.8)
        self.assertIsNotNone(otc["moving_averages"]["60"])
        self.assertIsNotNone(otc["volume_ratio_20d"])

    def test_auxiliary_data_cannot_move_score_more_than_ten_points(self):
        weights = {"trend_breadth": 0.25, "capital_flow": 0.20, "futures": 0.15}
        score, adjustment = auxiliary_adjustment(
            {"trend_breadth": 0, "capital_flow": 100, "futures": 100}, weights
        )
        self.assertEqual(score, 100)
        self.assertEqual(adjustment, 10)

    def test_exposure_blocks_leverage_when_indices_are_not_aligned(self):
        exposure = exposure_for_score(
            "強多",
            85,
            {"synchrony": "尚未同步", "coverage": 1.0},
            trend_history(True),
        )
        self.assertLessEqual(exposure["risk_adjusted_range"][1], 100)
        self.assertIn("加權與櫃買尚未同向", exposure["restrictions"])


if __name__ == "__main__":
    unittest.main()
