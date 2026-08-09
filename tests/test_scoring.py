import unittest

from tw_market_report.fixture import fixture_history
from tw_market_report.scoring import FEATURES, apply_overnight_overlay, classify_state, module_calculation_notes, reversal_stage, score_modules


class ScoringTests(unittest.TestCase):
    def test_free_official_mode_excludes_unobservable_bulk_features(self):
        retired = {
            "taiex_ma20_gap",
            "taiex_ma60_gap",
            "otc_ma20_gap",
            "above_ma20_ratio",
            "borrowed_sell_5d_change",
            "margin_stress_proxy",
            "noninst_short_long_ratio",
            "taiwan_vix_change_5d",
        }
        self.assertTrue(retired.isdisjoint(FEATURES))
        self.assertNotIn("foreign_futures_net_ratio", FEATURES)
        self.assertIn("foreign_futures_scheme7_score", FEATURES)

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

    def test_missing_features_are_neutral_instead_of_disappearing(self):
        scores, coverage, positive, negative = score_modules(
            {"futures_basis_pct": 0.01},
            [],
        )
        self.assertAlmostEqual(coverage["futures"], 1 / 2)
        self.assertEqual(scores["futures"], 50.0)
        self.assertEqual(positive, [])
        self.assertEqual(negative, [])

    def test_module_calculation_notes_disclose_observed_and_imputed_inputs(self):
        features = {"futures_basis_pct": -0.003}
        scores, _, _, _ = score_modules(features, [])
        notes = module_calculation_notes(features, [], scores)
        self.assertIn("1/2項實測", notes["futures"])
        self.assertIn("台指期正逆價差", notes["futures"])
        self.assertIn("缺1項以50分補齊", notes["futures"])
        self.assertIn("模組合成50.0分", notes["futures"])

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

    def test_reversal_stage_treats_missing_percentiles_as_neutral(self):
        stage, reasons = reversal_stage(
            {
                "margin_stress_percentile": None,
                "taiwan_vix_percentile": None,
                "valuation_stress_percentile": None,
            },
            "盤整",
            [],
        )
        self.assertEqual(stage, "無")
        self.assertEqual(reasons, [])


if __name__ == "__main__":
    unittest.main()
