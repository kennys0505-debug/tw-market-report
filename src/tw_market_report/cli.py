from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import load_config
from .backfill import HistoryBackfiller
from .backtest import run_limit_backtest
from .history import load_history, merge_history_files, write_json
from .notify import send_line
from .pipeline import ReportPipeline
from .render import render_dashboard
from .sources.calendar import is_taiwan_trading_day, latest_completed_trading_day


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Taiwan market regime report")
    result.add_argument("command", choices=["run", "fixture", "backfill", "backtest", "merge-history"], help="live run, fixture build, historical backfill, merge, or validation")
    result.add_argument("files", nargs="*", help="history chunks used by merge-history")
    result.add_argument("--mode", choices=["close", "premarket"], default="close")
    result.add_argument("--date", help="Taiwan trade date in YYYY-MM-DD")
    result.add_argument("--config", default="config/report.json")
    result.add_argument("--notify", action="store_true")
    result.add_argument("--report-url", default=os.environ.get("REPORT_URL", ""))
    result.add_argument("--start", help="backfill start date in YYYY-MM-DD")
    result.add_argument("--end", help="backfill end date in YYYY-MM-DD")
    result.add_argument("--delay", type=float, default=0.35, help="seconds between historical source requests")
    result.add_argument("--output", help="alternate JSONL output path for backfill or merge-history")
    result.add_argument("--skip-derivatives", action="store_true", help="omit TAIFEX historical requests")
    result.add_argument("--skip-overseas", action="store_true", help="omit Cboe and Yahoo historical requests")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_config(args.config)
    if args.command == "merge-history":
        if not args.files:
            print("ERROR: merge-history requires one or more input files", file=sys.stderr)
            return 2
        output = Path(args.output) if args.output else config.root / "data" / "history.jsonl"
        rows = merge_history_files([Path(value) for value in args.files], output)
        print(f"merge-history complete rows={len(rows)} output={output}")
        return 0
    if args.command == "backtest":
        result = run_limit_backtest(load_history(config.root / "data" / "history.jsonl"))
        write_json(config.root / "data" / "backtest.json", result)
        print(
            "backtest complete "
            f"improvement_pp={result['hit_rate_improvement_pp']} "
            f"limit_scoring_enabled={result['limit_scoring_enabled']}"
        )
        return 0
    if args.command == "backfill":
        if not args.start or not args.end:
            print("ERROR: backfill requires --start and --end", file=sys.stderr)
            return 2
        output = Path(args.output) if args.output else None
        written, skipped = HistoryBackfiller(config, args.delay, output).run(
            date.fromisoformat(args.start),
            date.fromisoformat(args.end),
            include_derivatives=not args.skip_derivatives,
            include_overseas=not args.skip_overseas,
        )
        print(f"backfill complete written={written} skipped={skipped}")
        return 0
    requested_date = date.fromisoformat(args.date) if args.date else None
    fixture = args.command == "fixture"
    now = datetime.now(ZoneInfo(config.raw.get("timezone", "Asia/Taipei")))
    run_date = requested_date
    if not fixture and run_date is None and args.mode == "close":
        run_date = latest_completed_trading_day(now, config.sources)
    effective_date = run_date or now.date()
    if not fixture and not is_taiwan_trading_day(effective_date, config.sources):
        print(f"Skip non-trading day: {effective_date}")
        return 0
    pipeline = ReportPipeline(config)
    try:
        snapshot = pipeline.run(args.mode, run_date, fixture=fixture)
        payload = pipeline.persist(snapshot, write_history=not fixture)
        render_dashboard(payload, pipeline.docs / "index.html")
        render_dashboard(payload, pipeline.docs / "archive" / f"{snapshot.trade_date}-{snapshot.report_mode}.html")
        notified = False
        if args.notify and not fixture:
            notified = send_line(
                payload,
                pipeline.digest(payload),
                pipeline.root / "data" / "notification_state.json",
                args.report_url,
            )
        print(
            f"built mode={snapshot.report_mode} date={snapshot.trade_date} "
            f"state={snapshot.domestic_market_state} score={snapshot.composite_score} notified={notified}"
        )
        return 0
    except Exception as error:
        if args.mode == "premarket" and "needs docs/close-latest.json" in str(error):
            print(f"Skip premarket report: {error}")
            return 0
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
