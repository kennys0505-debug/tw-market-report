import json
import tempfile
import unittest
from pathlib import Path

from tw_market_report.config import ReportConfig, load_config
from tw_market_report.notify import line_message
from tw_market_report.pipeline import ReportPipeline
from tw_market_report.render import render_dashboard


class PipelineTests(unittest.TestCase):
    def test_fixture_build_is_self_contained(self):
        raw = load_config().raw
        with tempfile.TemporaryDirectory() as directory:
            config = ReportConfig(raw=raw, root=Path(directory))
            pipeline = ReportPipeline(config)
            snapshot = pipeline.run("close", fixture=True)
            payload = pipeline.persist(snapshot, write_history=False)
            output = Path(directory) / "docs" / "index.html"
            render_dashboard(payload, output)
            html = output.read_text(encoding="utf-8")
            self.assertIn("台股每日多空", html)
            self.assertIn('id="snapshot"', html)
            self.assertNotIn("<script src=", html)
            self.assertGreater(payload["limit_up_count"], 0)
            self.assertEqual(payload["report_mode"], "close")
            self.assertTrue(all(value == 1.0 for value in payload["module_coverage"].values()))
            self.assertIn("module_observed_coverage", payload)
            self.assertIn("imputed_score_features", payload)

    def test_line_message_contains_required_limit_counts(self):
        message = line_message({
            "trade_date": "2026-07-28",
            "report_mode": "close",
            "domestic_market_state": "盤整",
            "composite_score": 50,
            "confidence": "中",
            "model_exposure_range": [40, 60],
            "limit_up_count": 12,
            "limit_down_count": 8,
            "limit_up_percentile_5y": 65,
            "limit_down_percentile_5y": 45,
        })
        self.assertIn("漲停 12 家", message)
        self.assertIn("跌停 8 家", message)


if __name__ == "__main__":
    unittest.main()
