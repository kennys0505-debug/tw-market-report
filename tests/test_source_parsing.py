import unittest
from datetime import date

from tw_market_report.sources.derivatives import DerivativesCollector
from tw_market_report.sources.domestic import DomesticCollector
from tw_market_report.sources.parsing import parse_tables


class FakeClient:
    def __init__(self, *, json_payload=None, text_payload=""):
        self.json_payload = json_payload
        self.text_payload = text_payload

    def get_json(self, _url):
        return self.json_payload

    def get_text(self, _url):
        return self.text_payload

    def post_form_text(self, _url, _fields):
        return self.text_payload


class SourceParsingTests(unittest.TestCase):
    def test_html_table_preserves_input_value(self):
        tables = parse_tables(
            '<table><tr><td>2026/07/29</td><td><input value="18.42" readonly></td></tr></table>'
        )
        self.assertEqual(tables[0][0], ["2026/07/29", "18.42"])

    def test_pc_ratio_chooses_latest_date_in_descending_table(self):
        html = """
        <table>
          <tr><td>2026/07/29</td><td>1</td><td>2</td><td>110</td><td>4</td><td>5</td><td>105</td></tr>
          <tr><td>2026/06/29</td><td>1</td><td>2</td><td>90</td><td>4</td><td>5</td><td>95</td></tr>
        </table>
        """
        collector = DerivativesCollector(
            {"taifex_pc_ratio": "https://example.test"},
            client=FakeClient(text_payload=html),
        )
        features, statuses = {}, []
        collector._pc_ratio(features, statuses, date(2026, 7, 29))
        self.assertEqual(statuses[0].as_of, "2026-07-29")
        self.assertAlmostEqual(features["put_call_oi_ratio"], 1.05)

    def test_taiwan_vix_uses_last_value_from_daily_file(self):
        collector = DerivativesCollector(
            {
                "taiwan_vix": "https://example.test/page",
                "taiwan_vix_data": "https://example.test/data?date={date}",
            },
            client=FakeClient(text_payload="09:00:15,18.20\n13:44:45 19.35\n"),
        )
        features, statuses = {}, []
        collector._taiwan_vix(features, statuses, date(2026, 7, 29))
        self.assertEqual(features["taiwan_vix"], 19.35)
        self.assertEqual(statuses[0].status, "ready")

    def test_futures_uses_settlement_column(self):
        html = """
        <table><tr><td>TX</td><td>202608</td><td>100</td><td>110</td><td>90</td>
        <td>105</td><td>5</td><td>5%</td><td>1000</td><td>103</td><td>2000</td></tr></table>
        """
        collector = DerivativesCollector(
            {"taifex_daily_futures": "https://example.test"},
            client=FakeClient(text_payload=html),
        )
        features, statuses = {}, []
        collector._futures(features, statuses, 100, date(2026, 7, 29))
        self.assertEqual(features["tx_settlement"], 103)
        self.assertEqual(features["futures_basis"], 3)
        self.assertEqual(features["tx_price_source"], "settlement")

    def test_futures_rejects_implausible_settlement_and_uses_last_trade(self):
        html = """
        <table><tr><td>TX</td><td>202608</td><td>100</td><td>110</td><td>90</td>
        <td>105</td><td>5</td><td>5%</td><td>1000</td><td>124405</td><td>2000</td></tr></table>
        """
        collector = DerivativesCollector(
            {"taifex_daily_futures": "https://example.test"},
            client=FakeClient(text_payload=html),
        )
        features, statuses = {}, []
        collector._futures(features, statuses, 100, date(2026, 7, 29))
        self.assertEqual(features["tx_settlement"], 105)
        self.assertEqual(features["tx_price_source"], "last_trade_fallback")
        self.assertEqual(statuses[0].status, "ready")

    def test_institution_positions_tracks_product_across_rowspans(self):
        html = """
        <table>
          <tr><td>1</td><td>臺股期貨</td><td>自營商</td><td>1</td><td>10</td></tr>
          <tr><td>外資</td><td>1</td><td>10</td><td>2</td><td>20</td><td>-100</td><td>-1000</td></tr>
          <tr><td>2</td><td>小型臺指期貨</td><td>自營商</td><td>1</td><td>10</td></tr>
          <tr><td>外資</td><td>1</td><td>10</td><td>2</td><td>20</td><td>40</td><td>400</td></tr>
        </table>
        """
        collector = DerivativesCollector(
            {"taifex_futures": "https://example.test"},
            client=FakeClient(text_payload=html),
        )
        features, statuses = {}, []
        collector._institution_positions(features, statuses, date(2026, 7, 29))
        self.assertEqual(features["foreign_tx_net"], -100)
        self.assertEqual(features["foreign_mtx_net"], 40)
        self.assertEqual(features["foreign_futures_net"], -90)

    def test_twse_numbered_fields_are_parsed(self):
        payload = {
            "fields9": ["證券代號", "本益比", "殖利率(%)", "股價淨值比"],
            "data9": [["2330", "24.5", "1.8", "6.2"]],
        }
        collector = DomesticCollector(
            {"twse_valuation": "https://example.test?date={date}"},
            client=FakeClient(json_payload=payload),
        )
        features, statuses = {}, []
        collector._collect_valuation("20260729", features, statuses)
        self.assertEqual(statuses[0].status, "ready")
        self.assertEqual(features["tsmc_pe"], 24.5)

    def test_twt72u_is_lending_balance_not_short_sale_balance(self):
        payload = {
            "fields": ["證券代號", "今日借券餘額(股)"],
            "data": [["2330", "1,200"], ["2317", "800"]],
        }
        collector = DomesticCollector(
            {"twse_lending": "https://example.test?date={date}"},
            client=FakeClient(json_payload=payload),
        )
        features, statuses = {}, []
        collector._collect_lending("20260729", features, statuses)
        self.assertEqual(features["borrowed_balance"], 2000)
        self.assertNotIn("borrowed_sell_balance", features)
        self.assertEqual(statuses[0].name, "TWSE借券餘額")


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import date

from tw_market_report.sources.derivatives import DerivativesCollector
from tw_market_report.sources.domestic import DomesticCollector
from tw_market_report.sources.parsing import parse_tables


class FakeClient:
    def __init__(self, *, json_payload=None, text_payload=""):
        self.json_payload = json_payload
        self.text_payload = text_payload

    def get_json(self, _url):
        return self.json_payload

    def get_text(self, _url):
        return self.text_payload

    def post_form_text(self, _url, _fields):
        return self.text_payload


class SourceParsingTests(unittest.TestCase):
    def test_html_table_preserves_input_value(self):
        tables = parse_tables(
            '<table><tr><td>2026/07/29</td><td><input value="18.42" readonly></td></tr></table>'
        )
        self.assertEqual(tables[0][0], ["2026/07/29", "18.42"])

    def test_pc_ratio_chooses_latest_date_in_descending_table(self):
        html = """
        <table>
          <tr><td>2026/07/29</td><td>1</td><td>2</td><td>110</td><td>4</td><td>5</td><td>105</td></tr>
          <tr><td>2026/06/29</td><td>1</td><td>2</td><td>90</td><td>4</td><td>5</td><td>95</td></tr>
        </table>
        """
        collector = DerivativesCollector(
            {"taifex_pc_ratio": "https://example.test"},
            client=FakeClient(text_payload=html),
        )
        features, statuses = {}, []
        collector._pc_ratio(features, statuses, date(2026, 7, 29))
        self.assertEqual(statuses[0].as_of, "2026-07-29")
        self.assertAlmostEqual(features["put_call_oi_ratio"], 1.05)

    def test_taiwan_vix_uses_last_value_from_daily_file(self):
        collector = DerivativesCollector(
            {
                "taiwan_vix": "https://example.test/page",
                "taiwan_vix_data": "https://example.test/data?date={date}",
            },
            client=FakeClient(text_payload="09:00:15,18.20\n13:44:45 19.35\n"),
        )
        features, statuses = {}, []
        collector._taiwan_vix(features, statuses, date(2026, 7, 29))
        self.assertEqual(features["taiwan_vix"], 19.35)
        self.assertEqual(statuses[0].status, "ready")

    def test_futures_uses_settlement_column(self):
        html = """
        <table><tr><td>TX</td><td>202608</td><td>100</td><td>110</td><td>90</td>
        <td>105</td><td>5</td><td>5%</td><td>1000</td><td>103</td><td>2000</td></tr></table>
        """
        collector = DerivativesCollector(
            {"taifex_daily_futures": "https://example.test"},
            client=FakeClient(text_payload=html),
        )
        features, statuses = {}, []
        collector._futures(features, statuses, 100, date(2026, 7, 29))
        self.assertEqual(features["tx_settlement"], 103)
        self.assertEqual(features["futures_basis"], 3)

    def test_institution_positions_tracks_product_across_rowspans(self):
        html = """
        <table>
          <tr><td>1</td><td>臺股期貨</td><td>自營商</td><td>1</td><td>10</td></tr>
          <tr><td>外資</td><td>1</td><td>10</td><td>2</td><td>20</td><td>-100</td><td>-1000</td></tr>
          <tr><td>2</td><td>小型臺指期貨</td><td>自營商</td><td>1</td><td>10</td></tr>
          <tr><td>外資</td><td>1</td><td>10</td><td>2</td><td>20</td><td>40</td><td>400</td></tr>
        </table>
        """
        collector = DerivativesCollector(
            {"taifex_futures": "https://example.test"},
            client=FakeClient(text_payload=html),
        )
        features, statuses = {}, []
        collector._institution_positions(features, statuses, date(2026, 7, 29))
        self.assertEqual(features["foreign_tx_net"], -100)
        self.assertEqual(features["foreign_mtx_net"], 40)
        self.assertEqual(features["foreign_futures_net"], -90)

    def test_twse_numbered_fields_are_parsed(self):
        payload = {
            "fields9": ["證券代號", "本益比", "殖利率(%)", "股價淨值比"],
            "data9": [["2330", "24.5", "1.8", "6.2"]],
        }
        collector = DomesticCollector(
            {"twse_valuation": "https://example.test?date={date}"},
            client=FakeClient(json_payload=payload),
        )
        features, statuses = {}, []
        collector._collect_valuation("20260729", features, statuses)
        self.assertEqual(statuses[0].status, "ready")
        self.assertEqual(features["tsmc_pe"], 24.5)

    def test_twt72u_is_lending_balance_not_short_sale_balance(self):
        payload = {
            "fields": ["證券代號", "今日借券餘額(股)"],
            "data": [["2330", "1,200"], ["2317", "800"]],
        }
        collector = DomesticCollector(
            {"twse_lending": "https://example.test?date={date}"},
            client=FakeClient(json_payload=payload),
        )
        features, statuses = {}, []
        collector._collect_lending("20260729", features, statuses)
        self.assertEqual(features["borrowed_balance"], 2000)
        self.assertNotIn("borrowed_sell_balance", features)
        self.assertEqual(statuses[0].name, "TWSE借券餘額")


if __name__ == "__main__":
    unittest.main()
