import unittest
from datetime import date, datetime

from tw_market_report.sources.calendar import CalendarUnavailableError, is_taiwan_trading_day, latest_completed_trading_day


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

    def test_close_run_before_cutoff_uses_previous_completed_session(self):
        rows = []
        resolved = latest_completed_trading_day(
            datetime(2026, 7, 30, 9, 0), {"twse_calendar": "x"}, FakeClient(rows)
        )
        self.assertEqual(resolved, date(2026, 7, 29))

    def test_actual_openapi_roc_date_and_substitute_holiday(self):
        for value in ('1151009', '115/10/09', '115-10-9', '20261009', '2026/10/09', '2026-10-09'):
            rows = [{'Name': '國慶日', 'Date': value,
                     'Description': '於10月9日（星期五）補假。'}]
            with self.subTest(value=value):
                self.assertFalse(is_taiwan_trading_day(date(2026, 10, 9),
                                 {'twse_calendar': 'x'}, FakeClient(rows), strict=True))

    def test_open_event_overrides_holiday_words_in_official_description(self):
        rows = [{'Date': '1150223', 'Name': '農曆春節後開始交易日', 'Description': '春節放假後開始交易。'}]
        self.assertTrue(is_taiwan_trading_day(date(2026, 2, 23), {'twse_calendar': 'x'}, FakeClient(rows), True))

    def test_unlisted_weekday_is_open_only_with_verified_annual_rows(self):
        rows = [{'Date': '1151009', 'Description': '補假'}]
        self.assertTrue(is_taiwan_trading_day(date(2026, 10, 8), {'twse_calendar': 'x'}, FakeClient(rows), True))

    def test_strict_calendar_rejects_unknown_schema_empty_invalid_date_or_wrong_year(self):
        for rows in ([], {}, [{'Unexpected': '1151009'}], [{'Date': '1150230'}],
                     [{'Date': '1141009', 'Description': '補假'}], [{'Date': '1151009', 'Name': '未知狀態'}]):
            with self.subTest(rows=rows), self.assertRaises(CalendarUnavailableError):
                is_taiwan_trading_day(date(2026, 10, 9), {'twse_calendar': 'x'}, FakeClient(rows), True)

    def test_strict_calendar_does_not_guess_on_network_failure(self):
        class BrokenClient:
            def get_json(self, url):
                raise TimeoutError('calendar timeout')
        with self.assertRaises(CalendarUnavailableError):
            is_taiwan_trading_day(date(2026, 10, 9), {'twse_calendar': 'x'}, BrokenClient(), True)

    def test_latest_close_during_national_day_long_weekend_is_thursday(self):
        rows = [{'Date': '1151009', 'Description': '補假'}]
        resolved = latest_completed_trading_day(datetime(2026, 10, 11, 6),
                                               {'twse_calendar': 'x'}, FakeClient(rows))
        self.assertEqual(resolved, date(2026, 10, 8))


if __name__ == "__main__":
    unittest.main()
