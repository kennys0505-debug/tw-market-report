import json
import tempfile
import unittest
from pathlib import Path

from tw_market_report.config import ReportConfig, load_config
from tw_market_report.notify import line_message
from tw_market_report.pipeline import ReportPipeline
from tw_market_report.render import render_dashboard


class PipelineTests(unittest.TestCase):
    def test_implausible_futures_history_is_sanitized(self) -> None:
        from tw_market_report.pipeline import _sanitize_history_row

        row = {
            "trade_date": "2026-07-29",
            "taiex_close": 40039.18,
            "features": {"tx_settlement": 124405, "futures_basis_pct": 2.107, "margin_balance": 1.0},
        }
        cleaned = _sanitize_history_row(row)
        self.assertNotIn("tx_settlement", cleaned["features"])
        self.assertNotIn("futures_basis_pct", cleaned["features"])
        self.assertEqual(cleaned["features"]["margin_balance"], 1.0)

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
            self.assertIn("module_history_coverage", payload)
            self.assertIn("imputed_score_features", payload)
            self.assertEqual(payload["features"]["active_feature_mode"], "free_official_proxy_v1")
            self.assertEqual(payload["imputed_score_features"], [])

    def test_failed_core_snapshot_is_not_written_to_history(self):
        raw = load_config().raw
        with tempfile.TemporaryDirectory() as directory:
            config = ReportConfig(raw=raw, root=Path(directory))
            pipeline = ReportPipeline(config)
            snapshot = pipeline.run("close", fixture=True)
            snapshot.features["core_data_ready"] = False
            pipeline.persist(snapshot, write_history=True)
            self.assertFalse((Path(directory) / "data" / "history.jsonl").exists())

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

import json
import tempfile
import unittest
from pathlib import Path

from tw_market_report.config import ReportConfig, load_config
from tw_market_report.notify import line_message
from tw_market_report.pipeline import ReportPipeline
from tw_market_report.render import render_dashboard


class PipelineTests(unittest.TestCase):
    def test_implausible_futures_history_is_sanitized(self) -> None:
        from tw_market_report.pipeline import _sanitize_history_row

        row = {
            "trade_date": "2026-07-29",
            "taiex_close": 40039.18,
            "features": {"tx_settlement": 124405, "futures_basis_pct": 2.107, "margin_balance": 1.0},
        }
        cleaned = _sanitize_history_row(row)
        self.assertNotIn("tx_settlement", cleaned["features"])
        self.assertNotIn("futures_basis_pct", cleaned["features"])
        self.assertEqual(cleaned["features"]["margin_balance"], 1.0)

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

    def test_failed_core_snapshot_is_not_written_to_history(self):
        raw = load_config().raw
        with tempfile.TemporaryDirectory() as directory:
            config = ReportConfig(raw=raw, root=Path(directory))
            pipeline = ReportPipeline(config)
            snapshot = pipeline.run("close", fixture=True)
            snapshot.features["core_data_ready"] = False
            pipeline.persist(snapshot, write_history=True)
            self.assertFalse((Path(directory) / "data" / "history.jsonl").exists())

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

