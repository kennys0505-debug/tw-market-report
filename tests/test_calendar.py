import unittest
from datetime import date

from tw_market_report.sources.calendar import is_taiwan_trading_day


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def get_json(self, url):
        return self.rows


class CalendarTests(unittest.TestCase):
    def test_weekend_is_closed_without_network(self):
        self.assertFalse(is_taiwan_trading_day(date(2026, 8, 1), {"twse_calendar": "x"}, FakeClient([])))

    def test_official_no_trading_marker(self):
        rows = [{"日期": "2026-02-12", "名稱": "市場無交易，僅辦理結算交割作業"}]
        self.assertFalse(is_taiwan_trading_day(date(2026, 2, 12), {"twse_calendar": "x"}, FakeClient(rows)))

    def test_last_trading_day_is_open(self):
        rows = [{"日期": "2026-02-11", "名稱": "農曆春節前最後交易日"}]
        self.assertTrue(is_taiwan_trading_day(date(2026, 2, 11), {"twse_calendar": "x"}, FakeClient(rows)))


if __name__ == "__main__":
    unittest.main()
