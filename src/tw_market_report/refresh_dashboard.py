"""Re-render saved report only: no downloads, history writes or notifications."""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from .presentation import prepare_dashboard
from .render import render_dashboard


def previous_archived(payload, directory):
    """Use the most recent saved report strictly before this report; no fake delta."""
    selected, selected_time = None, None
    current_time = datetime.fromisoformat(payload['generated_at'])
    for path in directory.glob('*.json'):
        with path.open(encoding='utf-8') as handle:
            header = handle.read(1500)
        match = re.search(r'"generated_at"\s*:\s*"([^"]+)"', header)
        if not match:
            continue
        try:
            timestamp = datetime.fromisoformat(match.group(1))
            eligible = timestamp < current_time and (selected_time is None or timestamp > selected_time)
        except (TypeError, ValueError):
            continue
        if eligible:
            selected, selected_time = path, timestamp
    return json.loads(selected.read_text(encoding='utf-8')) if selected else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='docs/latest.json')
    parser.add_argument('--output', default='docs/index.html')
    parser.add_argument('--previous', help='Optional actual previous report, never synthesize a comparison')
    parser.add_argument('--previous-archive', help='Directory containing actual archived reports')
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding='utf-8'))
    previous = json.loads(Path(args.previous).read_text(encoding='utf-8')) if args.previous else None
    if previous is None and args.previous_archive:
        previous = previous_archived(payload, Path(args.previous_archive))
    prepared = prepare_dashboard(payload, previous)
    render_dashboard(prepared, Path(args.output))
    print(f"Rendered existing {payload['trade_date']} {payload['report_mode']} report. Source timestamp unchanged; no data refresh or notifications.")


if __name__ == '__main__':
    main()
