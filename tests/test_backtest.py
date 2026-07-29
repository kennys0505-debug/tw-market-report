import unittest

from tw_market_report.backtest import run_limit_backtest
from tw_market_report.fixture import fixture_history


class BacktestTests(unittest.TestCase):
    def test_limit_gate_stays_closed_without_enough_evidence(self):
        result = run_limit_backtest(fixture_history()[:80], minimum_samples=30)
        self.assertFalse(result["limit_scoring_enabled"])
        self.assertIn("baseline_10d", result)
        self.assertIn("1d", result["limit_horizon_tests"])


if __name__ == "__main__":
    unittest.main()
