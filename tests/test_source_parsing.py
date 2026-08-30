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
    def test_tpex_index_history_populates_otc_ohlc_and_prior_closes(self):
        rows = [
            {"Date": f"2026/07/{day:02d}", "Open": str(200 + day), "High": str(202 + day),
             "Low": str(199 + day), "Close": str(201 + day)}
            for day in range(1, 30)
        ]
        collector = DomesticCollector(
            {"tpex_index_history": "https://example.test/tpex_index"},
            client=FakeClient(json_payload=rows),
        )
        features, statuses = {}, []
        collector._collect_tpex_index_history("20260729", features, statuses)
        self.assertEqual(features["otc_close"], 230)
        self.assertEqual(features["otc_open"], 229)
        self.assertEqual(len(features["otc_history"]), 28)
        self.assertEqual(statuses[0].status, "ready")

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
        self.assertNotIn("foreign_futures_net_ratio", features)

    def test_institution_positions_does_not_leak_into_other_products(self):
        html = """
        <table>
          <tr><td>1</td><td>臺股期貨</td><td>自營商</td><td>1</td><td>10</td></tr>
          <tr><td>外資</td><td>1</td><td>10</td><td>2</td><td>20</td><td>-100</td><td>-1000</td></tr>
          <tr><td>2</td><td>微型臺指期貨</td><td>自營商</td><td>1</td><td>10</td></tr>
          <tr><td>外資</td><td>1</td><td>10</td><td>2</td><td>20</td><td>40</td><td>400</td></tr>
          <tr><td>3</td><td>東證期貨</td><td>自營商</td><td>1</td><td>10</td></tr>
          <tr><td>外資</td><td>1</td><td>10</td><td>2</td><td>20</td><td>-999999</td><td>-9999990</td></tr>
        </table>
        """
        collector = DerivativesCollector(
            {"taifex_futures": "https://example.test"},
            client=FakeClient(text_payload=html),
        )
        features, statuses = {}, []
        collector._institution_positions(features, statuses, date(2026, 7, 29))
        self.assertEqual(features["foreign_tx_net"], -100)
        self.assertEqual(features["foreign_tmf_net"], 40)
        self.assertEqual(features["foreign_futures_net"], -98)

    def test_market_open_interest_requires_all_three_contract_sizes(self):
        class OIClient:
            def post_form_text(self, _url, fields):
                product = fields["commodity_id"]
                return f"""
                <table><tr><td>2026/07/29</td></tr></table>
                <table><tr><td>{product}</td><td>202608</td><td>100</td><td>110</td><td>90</td>
                <td>105</td><td>5</td><td>5%</td><td>10</td><td>20</td><td>30</td><td>103</td><td>1000</td></tr></table>
                """

        collector = DerivativesCollector(
            {"taifex_daily_futures": "https://example.test"},
            client=OIClient(),
        )
        features, statuses = {}, []
        collector._market_open_interest(features, statuses, date(2026, 7, 29))
        self.assertEqual(features["futures_market_oi"], 1300)
        self.assertEqual(statuses[0].status, "ready")

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

    def test_institutional_uses_buy_sell_difference_and_sums_dealer_rows(self):
        payload = {
            "stat": "OK",
            "fields": ["單位名稱", "買進金額", "賣出金額", "買賣差額"],
            "data": [
                ["自營商(自行買賣)", "120", "100", "20"],
                ["自營商(避險)", "80", "110", "-30"],
                ["投信", "150", "100", "50"],
                ["外資及陸資(不含外資自營商)", "500", "400", "100"],
                ["外資自營商", "0", "0", "0"],
                ["合計", "850", "710", "140"],
            ],
        }
        collector = DomesticCollector(
            {"twse_institutional": "https://example.test?date={date}"},
            client=FakeClient(json_payload=payload),
        )
        features, statuses = {"market_turnover": 1000}, []
        collector._collect_institutional("20260805", features, statuses)
        self.assertAlmostEqual(features["foreign_flow_ratio"], 0.10)
        self.assertAlmostEqual(features["trust_flow_ratio"], 0.05)
        self.assertAlmostEqual(features["dealer_flow_ratio"], -0.01)
        self.assertEqual(statuses[0].status, "ready")

    def test_institutional_missing_values_are_not_reported_as_zero(self):
        payload = {
            "stat": "OK",
            "fields": ["單位名稱", "買進金額", "賣出金額"],
            "data": [["投信", "無資料", "無資料"]],
        }
        collector = DomesticCollector(
            {"twse_institutional": "https://example.test?date={date}"},
            client=FakeClient(json_payload=payload),
        )
        features, statuses = {"market_turnover": 1000}, []
        collector._collect_institutional("20260805", features, statuses)
        self.assertNotIn("foreign_flow_ratio", features)
        self.assertNotIn("trust_flow_ratio", features)
        self.assertNotIn("dealer_flow_ratio", features)
        self.assertEqual(statuses[0].status, "partial")


if __name__ == "__main__":
    unittest.main()
