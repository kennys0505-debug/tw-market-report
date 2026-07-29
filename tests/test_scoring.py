import unittest

from tw_market_report.fixture import fixture_history
from tw_market_report.scoring import apply_overnight_overlay, classify_state, score_modules


class ScoringTests(unittest.TestCase):
    def test_module_scores_are_bounded_and_cover_features(self):
        history = fixture_history()
        features = history[-1]["features"]
        scores, coverage, positive, negative = score_modules(features, history[:-1])
        self.assertEqual(set(scores), set(coverage))
        self.assertTrue(all(0 <= value <= 100 for value in scores.values()))
        self.assertGreater(coverage["trend_breadth"], 0.8)
        self.assertTrue(positive or negative)

    def test_state_requires_module_agreement(self):
        modules = {name: 75 for name in ["a", "b", "c", "d", "e", "f", "g"]}
        self.assertEqual(classify_state(70, modules, []), "強多")
        modules = {name: 50 for name in modules}
        self.assertEqual(classify_state(70, modules, []), "盤整")

    def test_turn_state_requires_market_confirmation(self):
        modules = {name: 65 for name in ["a", "b", "c", "d", "e", "f", "g"]}
        history = [{"composite_score": 62, "domestic_market_state": "盤整"}]
        self.assertEqual(classify_state(62, modules, history, {}), "盤整")
        self.assertEqual(classify_state(62, modules, history, {"limit_breadth": 0.01}), "轉多")

    def test_overnight_overlay_is_capped(self):
        state, adjustment = apply_overnight_overlay({
            "us_vix_change_5d": 0.20,
            "sox_return_5d": -0.08,
            "tsm_adr_premium": -0.03,
            "usd_twd_change_5d": 0.02,
        })
        self.assertEqual(state, "高風險")
        self.assertEqual(adjustment, -10)


if __name__ == "__main__":
    unittest.main()
