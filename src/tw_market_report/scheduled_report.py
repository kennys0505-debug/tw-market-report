"""Idempotent publication slots for delayed scheduled runs; no score changes."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import cli
from .config import load_config
from .history import load_json
from .sources.http import HttpClient
from .notify import send_line
from .pipeline import ReportPipeline
from .presentation import finite, summary, next_deadline
from .sources.calendar import is_taiwan_trading_day

TZ = ZoneInfo('Asia/Taipei')
PREMARKET_START = time(8, 7)
CLOSE_START = time(21, 53)


@dataclass(frozen=True)
class Slot:
    mode: str
    day: date


def weekday(day):
    return day.weekday() < 5


def previous_day(day, is_trading_day=weekday):
    for offset in range(1, 22):
        candidate = day - timedelta(days=offset)
        if is_trading_day(candidate):
            return candidate
    raise ValueError('No preceding trading day found; refusing to guess')


def select_slot(now, is_trading_day=weekday):
    if now.tzinfo is None:
        raise ValueError('A timezone-aware clock is required')
    local = now.astimezone(TZ)
    today = local.date()
    if is_trading_day(today):
        if local.time() >= CLOSE_START:
            return Slot('close', today)
        if local.time() >= PREMARKET_START:
            return Slot('premarket', today)
    return Slot('close', previous_day(today, is_trading_day))


def slot_identity(payload):
    try:
        mode = payload['report_mode']
        if mode not in ('close', 'premarket'):
            return None
        return date.fromisoformat(payload['trade_date']), int(mode == 'close')
    except (KeyError, TypeError, ValueError):
        return None


def core_ready(payload, expected_day):
    features = payload.get('features') or {}
    day = str(features.get('trade_date') or payload.get('trade_date', '')).replace('-', '')
    if features.get('core_data_ready') is not True or day != expected_day.strftime('%Y%m%d'):
        return False
    technical = payload.get('technical_analysis') or {}
    for market in ('taiex', 'otc'):
        item = technical.get(market) or {}
        if not finite(item.get('close')) or not finite(item.get('coverage')) or item['coverage'] < .8:
            return False
    for source in payload.get('source_status', []):
        if source.get('status') == 'fixture':
            return False
        if source.get('name') in ('TWSE收盤行情', 'TPEx市場現況'):
            as_of = str(source.get('as_of') or '').replace('-', '').replace('/', '')
            if source.get('status') != 'ready' or as_of != day:
                return False
    return True


def is_current(payload, slot, now, expected_core_day=None):
    if now.tzinfo is None:
        raise ValueError('A timezone-aware clock is required')
    if slot_identity(payload) != (slot.day, int(slot.mode == 'close')):
        return False
    expected_core_day = expected_core_day or (slot.day if slot.mode == 'close' else previous_day(slot.day))
    if not core_ready(payload, expected_core_day):
        return False
    try:
        generated = datetime.fromisoformat(payload['generated_at'])
        if generated.tzinfo is None or generated > now + timedelta(minutes=5):
            return False
        start = datetime.combine(slot.day, CLOSE_START if slot.mode == 'close' else PREMARKET_START, TZ)
        if generated < start:
            return False
        view = summary(payload, now=now)
        saved = payload.get('decision_summary') or {}
        return (view['status'] == 'ready' and saved.get('status') == 'ready'
                and saved.get('valid_until') == view['valid_until'])
    except (KeyError, TypeError, ValueError):
        return False


def run(config_path='config/report.json', requested_mode='auto', force=False,
        notify=False, now=None):
    clock_was_injected = now is not None
    now = now or datetime.now(TZ)
    config = load_config(config_path)
    client = HttpClient(timeout=12, retries=1)
    calendar_cache = {}
    def trading_day(day):
        if day not in calendar_cache:
            calendar_cache[day] = is_taiwan_trading_day(day, config.sources, client)
        return calendar_cache[day]

    slot = select_slot(now, trading_day)
    if requested_mode not in ('auto', slot.mode):
        raise ValueError(f'Current publication slot is {slot.mode}; refusing an out-of-order {requested_mode} report')
    docs = config.root / 'docs'
    latest = load_json(docs / 'latest.json', {}) or {}
    identity = slot_identity(latest)
    if identity and identity > (slot.day, int(slot.mode == 'close')):
        raise ValueError('A newer report already exists; refusing to overwrite it')
    core_day = slot.day if slot.mode == 'close' else previous_day(slot.day, trading_day)
    current = is_current(latest, slot, now, core_day)
    if now > next_deadline(now, slot.mode, slot.day.isoformat()):
        return {'publish': False, 'changed': False, 'mode': slot.mode, 'date': slot.day.isoformat(),
                'reason': 'No currently valid publication slot; keep the expired-data protection'}
    built = force or not current
    if built:
        previous_generated = latest.get('generated_at')
        if slot.mode == 'premarket':
            close = load_json(docs / 'close-latest.json', {}) or {}
            if slot_identity(close) != (core_day, 1) or not core_ready(close, core_day):
                # Keep the prerequisite local until the final report passes checks.
                result = cli.main(['run', '--mode', 'close', '--date', core_day.isoformat(), '--config', str(config_path)])
                close = load_json(docs / 'close-latest.json', {}) or {}
                if result or slot_identity(close) != (core_day, 1) or not core_ready(close, core_day):
                    raise RuntimeError('Previous close is not complete; no new premarket report published')
        if cli.main(['backtest', '--config', str(config_path)]):
            raise RuntimeError('Existing limit-signal validation gate failed')
        result = cli.main(['run', '--mode', slot.mode, '--date', slot.day.isoformat(), '--config', str(config_path)])
        latest = load_json(docs / 'latest.json', {}) or {}
        finished = now if clock_was_injected else datetime.now(TZ)
        if select_slot(finished, trading_day) != slot:
            raise RuntimeError('Publication slot changed during generation; leave publication to the next run')
        # A zero CLI exit can mean a holiday skip. Require usable new data.
        if (result or latest.get('generated_at') == previous_generated
                or not is_current(latest, slot, finished, core_day)):
            raise RuntimeError('New report failed date/core/freshness validation; no deployment permitted')
    notified = False
    if notify:
        state_path = config.root / 'data/notification_state.json'
        state = load_json(state_path, {}) or {}
        key = f'{slot.day.isoformat()}:{slot.mode}'
        # A primary can notify a backup-built report; a changed digest alone
        # must not send a second notification for the same saved publication slot.
        if key not in state:
            notified = send_line(latest, ReportPipeline.digest(latest), state_path, os.environ.get('REPORT_URL', ''))
    # Republish a verified current slot even without new collection. A previous
    # run may have committed its data successfully but failed to deploy Pages.
    return {'publish': True, 'changed': built or notified, 'mode': slot.mode,
            'date': slot.day.isoformat(), 'notified': notified,
            'reason': 'Built and validated' if built else 'Current slot already complete'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/report.json')
    parser.add_argument('--mode', choices=['auto', 'close', 'premarket'], default='auto')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--notify', action='store_true')
    parser.add_argument('--github-output')
    args = parser.parse_args()
    result = run(args.config, args.mode, args.force, args.notify)
    print(json.dumps(result, ensure_ascii=False))
    if args.github_output:
        with Path(args.github_output).open('a', encoding='utf-8') as handle:
            for key in ('publish', 'changed', 'mode', 'date'):
                value = str(result[key]).lower() if isinstance(result[key], bool) else result[key]
                handle.write(f'{key}={value}\n')


if __name__ == '__main__':
    main()
