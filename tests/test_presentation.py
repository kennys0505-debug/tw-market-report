import copy
import unittest
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

from tw_market_report.presentation import summary, prepare_dashboard, next_deadline
from tw_market_report.technical import _chart_rows, technical_analysis

TZ = ZoneInfo('Asia/Taipei')
NOW = datetime(2026, 9, 11, 7, 30, tzinfo=TZ)


def sample():
    return {'generated_at': '2026-09-11T01:18:15+08:00', 'trade_date': '2026-09-10', 'report_mode': 'close',
            'features': {'trade_date': '2026-09-10', 'core_data_ready': True},
            'exposure_details': {'center': 67},
            'technical_analysis': {'taiex': {'signal': '強多', 'coverage': 1, 'close': 46940},
                                   'otc': {'signal': '轉多', 'coverage': 1, 'close': 405}}}


class PresentationTests(unittest.TestCase):
    def test_mixed_strength_is_bullish_not_disagreement(self):
        view = summary(sample(), now=NOW)
        self.assertEqual(view['signal'], '偏多')
        self.assertEqual(view['synchrony'], '兩者偏多，強弱不同')
        self.assertEqual((view['target_percent'], view['cash_percent']), (67, 33))

    def test_divergence_and_flat_stay_wait(self):
        for signal in ('轉空', '強空', '盤整'):
            payload = sample(); payload['technical_analysis']['otc']['signal'] = signal
            self.assertEqual(summary(payload, now=NOW)['signal'], '觀望')

    def test_both_bearish(self):
        payload = sample()
        for x in payload['technical_analysis'].values(): x['signal'] = '轉空'
        self.assertEqual(summary(payload, now=NOW)['signal'], '偏空')

    def test_missing_stale_and_nonfinite_hide_allocation(self):
        for mutation in ('coverage', 'core', 'stale', 'timestamp', 'target', 'source'):
            with self.subTest(mutation=mutation):
                p = sample()
                if mutation == 'coverage': p['technical_analysis']['otc']['coverage'] = .79
                if mutation == 'core': p['features']['core_data_ready'] = False
                if mutation == 'stale': p['generated_at'] = '2026-09-09T22:15:00+08:00'
                if mutation == 'timestamp': p['generated_at'] = 'invalid'
                if mutation == 'target': p['exposure_details']['center'] = float('nan')
                if mutation == 'source': p['source_status'] = [{'name':'TWSE收盤行情','status':'ready','as_of':'20260909'}]
                result = summary(p, now=NOW)
                self.assertEqual(result['signal'], '暫停判斷')
                self.assertIsNone(result['target_percent'])
                self.assertIsNone(result['cash_percent'])

    def test_previous_delta_and_unknown_are_honest(self):
        p = sample(); old = sample(); old['generated_at'] = '2026-09-10T22:10:00+08:00'
        for old_target, expected in ((60, 7), (70, -3), (67, 0)):
            old['exposure_details']['center'] = old_target
            result = summary(p, old, NOW)
            self.assertEqual(result['change_pp'], expected)
            if expected == 0: self.assertEqual(result['change_text'], '目標水位不變')
        self.assertIn('尚無可比較', summary(p, now=NOW)['change_text'])
        self.assertIn('尚無可比較', summary(p, p, NOW)['change_text'])

    def test_refresh_keeps_source_and_saved_comparison(self):
        p = sample(); before = copy.deepcopy(p)
        prepared = prepare_dashboard(p, now=NOW)
        prepared['decision_summary']['change_text'] = '目標水位不變'
        again = prepare_dashboard(prepared, now=NOW)
        self.assertEqual(again['decision_summary']['change_text'], '目標水位不變')
        self.assertEqual(p, before)
        self.assertEqual(again['generated_at'], p['generated_at'])
        self.assertEqual(again['exposure_details'], p['exposure_details'])

    def test_refresh_deadline_preserves_weekend(self):
        due = next_deadline(datetime(2026,9,11,22,40,tzinfo=TZ))
        self.assertEqual(due, datetime(2026,9,14,8,45,tzinfo=TZ))

    def test_premarket_uses_prior_close_date(self):
        p = sample(); p['report_mode'] = 'premarket'; p['trade_date'] = '2026-09-11'
        p['generated_at'] = '2026-09-11T08:15:00+08:00'
        self.assertEqual(summary(p, now=datetime(2026,9,11,8,20,tzinfo=TZ))['data_date'], '2026-09-10')


class ChartEventsTests(unittest.TestCase):
    def prices(self):
        values = [200-i for i in range(70)] + [130+i*2 for i in range(60)] + [248-i*3 for i in range(45)]
        return [{'date':(date(2025,1,1)+timedelta(days=i)).isoformat(), 'close':v, 'open':v-.2,'high':v+1,'low':v-1} for i,v in enumerate(values)]

    def test_both_directions_with_reasons_and_no_lookahead(self):
        prices = self.prices()
        all_rows = _chart_rows('taiex', {'taiex_price_history': prices}, [], '盤整')
        labels = {e['label'] for row in all_rows for e in row['signal_events']}
        self.assertTrue({'短線轉強','短線轉弱','中期轉多','中期轉空'} <= labels)
        for n in range(80, len(prices)+1):
            prefix = _chart_rows('taiex', {'taiex_price_history':prices[:n]}, [], '盤整')[-1]
            full = next((r for r in all_rows if r['date']==prefix['date']), None)
            if full:
                self.assertEqual(prefix['signal_events'], full['signal_events'])
                self.assertEqual(prefix['ma20'], full['ma20'])

    def test_date_normalization_dedup_flat_close_and_future_exclusion(self):
        rows = _chart_rows('taiex', {'trade_date':'2026-09-10','taiex_close':100,
            'taiex_price_history':[{'date':'20260909','close':100},{'date':'2026-09-09','close':101},
                                   {'date':'2026-09-11','close':999}]}, [], '盤整')
        self.assertEqual([r['date'] for r in rows], ['2026-09-09','2026-09-10'])
        self.assertEqual(rows[0]['close'],101)
        self.assertTrue(all(r['price_kind']=='close_only' for r in rows))

    def test_history_warms_up_source_ohlc_without_faking_candles(self):
        prices=self.prices()
        history=[{'trade_date':r['date'],'taiex_close':r['close']} for r in prices[:120]]
        rows=_chart_rows('taiex',{'taiex_price_history':prices[110:]},history,'盤整')
        self.assertIsNotNone(rows[0]['ma60'])
        self.assertEqual(rows[0]['price_kind'],'close_only')
        self.assertEqual(rows[-1]['price_kind'],'ohlc')


if __name__ == '__main__': unittest.main()
