"""Read-only decision summary. Never changes scores, allocations or source dates."""
from __future__ import annotations

import copy
import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .technical import _chart_rows

TZ = ZoneInfo('Asia/Taipei')
BULL = {'強多', '轉多'}
BEAR = {'強空', '轉空'}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def next_deadline(generated):
    """Conservative publication SLA, not an exchange trading-calendar claim."""
    for offset in range(8):
        day = generated.date() + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for hour, minute in ((8, 45), (22, 30)):
            due = datetime.combine(day, time(hour, minute), TZ)
            if due > generated:
                return due
    raise ValueError('No refresh deadline')


def summary(payload, previous=None, now=None):
    now = now or datetime.now(TZ)
    technical = payload.get('technical_analysis') or {}
    taiex, otc = (technical.get(k) or {} for k in ('taiex', 'otc'))
    a, b = taiex.get('signal'), otc.get('signal')
    if a in BULL and b in BULL:
        signal, sync = '偏多', '兩者偏多，強弱不同' if a != b else '兩者偏多，同向確認'
        reason = '加權與櫃買均偏多；依既有模型水位控管曝險，不因單日上漲追高。'
    elif a in BEAR and b in BEAR:
        signal, sync = '偏空', '兩者偏空，強弱不同' if a != b else '兩者偏空，同向確認'
        reason = '加權與櫃買均偏空；以降低多方曝險、保留現金為主，不代表必須放空。'
    else:
        signal = '觀望'
        sync = '多空分歧' if (a in BULL and b in BEAR) or (b in BULL and a in BEAR) else '方向尚未一致'
        reason = '加權與櫃買方向分歧或仍在盤整，等待同向確認；觀望不等於全數出清。'
    price_reasons = []
    for label, item in (('加權', taiex), ('櫃買', otc)):
        close = item.get('close')
        averages = item.get('moving_averages') or {}
        if finite(close) and all(finite(averages.get(k)) for k in ('20', '60')):
            if close > max(averages['20'], averages['60']):
                price_reasons.append(label+'收盤站上20、60日線')
            elif close < min(averages['20'], averages['60']):
                price_reasons.append(label+'收盤在20、60日線之下')
            else:
                price_reasons.append(label+'收盤介於20、60日線之間')
    if price_reasons:
        reason = '；'.join(price_reasons)+'。'+reason
    issues, valid_until = [], None
    try:
        generated = datetime.fromisoformat(payload['generated_at'])
        if generated.tzinfo is None:
            raise ValueError('timestamp timezone missing')
        generated = generated.astimezone(TZ)
        valid_until = next_deadline(generated)
        if generated > now + timedelta(minutes=5):
            issues.append('報告時間異常')
        if now > valid_until:
            issues.append('已超過預期更新時限，請等待新版報告（休市日亦採保守暫停）')
    except (KeyError, TypeError, ValueError):
        issues.append('缺少可核對的更新時間')
    features = payload.get('features') or {}
    if features.get('core_data_ready') is not True:
        issues.append('核心行情尚未就緒')
    if any(not finite(x.get('coverage')) or x['coverage'] < .8 or not finite(x.get('close')) for x in (taiex, otc)):
        issues.append('加權或櫃買技術資料覆蓋不足80%')
    data_day = str(features.get('trade_date') or payload.get('trade_date') or '')
    if len(data_day) == 8 and data_day.isdigit():
        data_day = f'{data_day[:4]}-{data_day[4:6]}-{data_day[6:]}'
    try:
        age = (now.date() - date.fromisoformat(data_day)).days
        if age < 0 or age > 4:
            issues.append('國內行情日期過舊或異常')
    except ValueError:
        issues.append('國內行情日期無法核對')
    core_names = {'TWSE收盤行情', 'TPEx市場現況'}
    for status in payload.get('source_status', []):
        if status.get('name') in core_names:
            day = str(status.get('as_of') or '').replace('-', '').replace('/', '')
            if status.get('status') != 'ready' or day != data_day.replace('-', ''):
                issues.append(status['name']+'資料不足或日期不符')
    if any(s.get('status') == 'fixture' for s in payload.get('source_status', [])):
        issues.append('示範資料不提供判斷')
    target = (payload.get('exposure_details') or {}).get('center')
    if not finite(target) or not 0 <= target <= 150:
        issues.append('缺少有效模型水位')
    blocked = bool(issues)
    change = '尚無可比較的上一份水位'
    delta = None
    previous_at = None
    if not blocked and previous:
        old_view = previous.get('decision_summary') or {}
        old = old_view.get('target_percent') if old_view else (previous.get('exposure_details') or {}).get('center')
        old_time = previous.get('generated_at', '')
        if finite(old) and old_time and old_time < payload.get('generated_at', '') and old_view.get('status') != 'paused':
            delta = round(target) - round(old)
            previous_at = old_time
            change = '目標水位不變' if delta == 0 else f"較上一份報告{'增加' if delta > 0 else '減少'} {abs(delta)} 個百分點"
    adjustment_reason = ''
    if delta:
        adjustment_reason = '依本次技術狀態與波動風險上限重新計算。'
        prior_score, score = previous.get('technical_score'), payload.get('technical_score')
        if finite(prior_score) and finite(score) and prior_score != score:
            adjustment_reason = f'技術主分數由{prior_score:.1f}變為{score:.1f}，再依波動風險上限調整。'
        if payload.get('report_mode') == 'premarket':
            adjustment_reason = '依隔夜海外風險調整；國內技術依據仍為最近收盤。'
    return {'version': 1, 'status': 'paused' if blocked else 'ready',
            'signal': '暫停判斷' if blocked else signal, 'synchrony': sync,
            'explanation': '；'.join(dict.fromkeys(issues)) if blocked else reason,
            'target_percent': None if blocked else round(target),
            'cash_percent': None if blocked else max(0, 100-round(target)),
            'change_text': '等待資料恢復，不產生調整建議' if blocked else change,
            'change_pp': delta, 'adjustment_reason': adjustment_reason,
            'previous_generated_at': previous_at,
            'valid_until': valid_until.isoformat() if valid_until else None,
            'data_date': data_day,
            'exposure_note': '模型研究值，未經完整十年策略驗證；不是個人化最佳水位。',
            'leveraged': bool(not blocked and target > 100)}


def prepare_dashboard(payload, previous=None, now=None):
    result = copy.deepcopy(payload)
    technical = result.setdefault('technical_analysis', {})
    features = dict(result.get('features') or {})
    features.setdefault('trade_date', result.get('trade_date'))
    for market in ('taiex', 'otc'):
        item = technical.get(market)
        if not item:
            continue
        # Re-render existing observations, never refresh scores or claim new data.
        if item.get('chart_version') != 2:
            item['chart'] = _chart_rows(market, features, result.get('history', []), item.get('signal', '資料不足'))
            item['chart_version'] = 2
    existing = result.get('decision_summary')
    view = summary(result, previous, now)
    if previous is None and existing and view['status'] == 'ready':
        for key in ('change_text', 'change_pp', 'adjustment_reason', 'previous_generated_at'):
            view[key] = existing.get(key, view[key])
    result['decision_summary'] = view
    technical['synchrony'] = view['synchrony']
    return result
