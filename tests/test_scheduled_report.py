import copy
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tw_market_report import scheduled_report as scheduled
from tw_market_report.presentation import summary
from tw_market_report.scheduled_report import Slot, is_current, select_slot, slot_identity


TZ = ZoneInfo("Asia/Taipei")


def taipei(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=TZ)


def ready_payload(mode="premarket", day=date(2026, 10, 8), core_day=None, generated=None):
    core_day = core_day or (day if mode == "close" else date(2026, 10, 7))
    generated = generated or datetime.combine(day, scheduled.CLOSE_START if mode == "close" else scheduled.PREMARKET_START, TZ)
    payload = {
        "trade_date": day.isoformat(),
        "report_mode": mode,
        "generated_at": generated.isoformat(),
        "features": {"trade_date": core_day.isoformat(), "core_data_ready": True},
        "technical_analysis": {
            "taiex": {"signal": "強多", "coverage": 1.0, "close": 100},
            "otc": {"signal": "轉多", "coverage": 1.0, "close": 200},
        },
        "exposure_details": {"center": 60},
        "source_status": [
            {"name": name, "status": "ready", "as_of": core_day.strftime("%Y%m%d")}
            for name in ("TWSE收盤行情", "TPEx市場現況")
        ],
    }
    payload["decision_summary"] = summary(payload, now=generated)
    return payload


class SelectSlotTests(unittest.TestCase):
    def test_actual_execution_time_controls_delayed_schedules(self):
        cases = (
            (taipei(2026, 10, 8, 8, 6, 59), Slot("close", date(2026, 10, 7))),
            (taipei(2026, 10, 8, 8, 7), Slot("premarket", date(2026, 10, 8))),
            (taipei(2026, 10, 8, 21, 52, 59), Slot("premarket", date(2026, 10, 8))),
            (taipei(2026, 10, 8, 21, 53), Slot("close", date(2026, 10, 8))),
            (taipei(2026, 10, 9, 0, 1), Slot("close", date(2026, 10, 8))),
        )
        for now, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(select_slot(now), expected)

    def test_weekend_keeps_last_trading_close(self):
        for now in (taipei(2026, 10, 10, 12), taipei(2026, 10, 11, 23)):
            with self.subTest(now=now):
                self.assertEqual(select_slot(now), Slot("close", date(2026, 10, 9)))

    def test_monday_before_morning_slot_uses_friday_close(self):
        self.assertEqual(
            select_slot(taipei(2026, 10, 12, 8, 6)),
            Slot("close", date(2026, 10, 9)),
        )

    def test_holiday_and_long_weekend_use_injected_calendar(self):
        holidays = {date(2026, 10, 9)}

        def is_open(day):
            return day.weekday() < 5 and day not in holidays

        for now in (taipei(2026, 10, 9, 10), taipei(2026, 10, 12, 8, 6)):
            with self.subTest(now=now):
                self.assertEqual(
                    select_slot(now, is_trading_day=is_open),
                    Slot("close", date(2026, 10, 8)),
                )
        self.assertEqual(
            select_slot(taipei(2026, 10, 12, 8, 7), is_trading_day=is_open),
            Slot("premarket", date(2026, 10, 12)),
        )

    def test_utc_timestamp_is_converted_before_slot_selection(self):
        self.assertEqual(
            select_slot(datetime(2026, 10, 8, 0, 7, tzinfo=timezone.utc)),
            Slot("premarket", date(2026, 10, 8)),
        )
        self.assertEqual(
            select_slot(datetime(2026, 10, 8, 16, 1, tzinfo=timezone.utc)),
            Slot("close", date(2026, 10, 8)),
        )

    def test_naive_timestamp_is_not_silently_interpreted_as_host_time(self):
        with self.assertRaises(ValueError):
            select_slot(datetime(2026, 10, 8, 8, 7))


class SlotIdentityTests(unittest.TestCase):
    def test_slot_order_never_demotes_a_newer_report(self):
        previous_close = slot_identity({"trade_date": "2026-10-07", "report_mode": "close"})
        morning = slot_identity({"trade_date": "2026-10-08", "report_mode": "premarket"})
        close = slot_identity({"trade_date": "2026-10-08", "report_mode": "close"})
        self.assertIsNotNone(previous_close)
        self.assertIsNotNone(morning)
        self.assertIsNotNone(close)
        self.assertLess(previous_close, morning)
        self.assertLess(morning, close)

    def test_invalid_payload_cannot_claim_a_completed_slot(self):
        cases = (
            None,
            {},
            {"trade_date": "invalid", "report_mode": "close"},
            {"trade_date": "2026-02-30", "report_mode": "close"},
            {"trade_date": "2026-10-08", "report_mode": "fixture"},
            {"trade_date": "2026-10-08"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertIsNone(slot_identity(payload))


class CurrentSlotTests(unittest.TestCase):
    def setUp(self):
        self.slot = Slot("premarket", date(2026, 10, 8))
        self.now = taipei(2026, 10, 8, 9)

    def test_usable_saved_report_is_current(self):
        self.assertTrue(is_current(ready_payload(), self.slot, self.now))

    def test_wrong_slot_is_not_current(self):
        for payload in (
            ready_payload("close", day=date(2026, 10, 7)),
            ready_payload("premarket", day=date(2026, 10, 7), core_day=date(2026, 10, 6)),
        ):
            with self.subTest(payload=payload["trade_date"]):
                self.assertFalse(is_current(payload, self.slot, self.now))

    def test_premarket_requires_the_correct_previous_close(self):
        payload = ready_payload(core_day=date(2026, 10, 6))
        self.assertFalse(is_current(payload, self.slot, self.now))
        # The official holiday calendar can override the weekday-only default.
        self.assertTrue(is_current(payload, self.slot, self.now, expected_core_day=date(2026, 10, 6)))

    def test_saved_ready_flag_does_not_bypass_core_or_fixture_checks(self):
        for mutation in ("missing_core", "coverage", "nonfinite_close", "wrong_source_date", "fixture"):
            payload = ready_payload()
            if mutation == "missing_core":
                payload["features"]["core_data_ready"] = False
            elif mutation == "coverage":
                payload["technical_analysis"]["otc"]["coverage"] = .79
            elif mutation == "nonfinite_close":
                payload["technical_analysis"]["taiex"]["close"] = float("nan")
            elif mutation == "wrong_source_date":
                payload["source_status"][0]["as_of"] = "20261006"
            elif mutation == "fixture":
                payload["source_status"][0]["status"] = "fixture"
            with self.subTest(mutation=mutation):
                self.assertFalse(is_current(payload, self.slot, self.now))

    def test_bad_generation_time_is_not_current(self):
        for value in ("invalid", "2026-10-08T08:15:00", "2026-10-08T08:06:59+08:00", "2026-10-08T09:06:00+08:00"):
            payload = ready_payload()
            payload["generated_at"] = value
            with self.subTest(value=value):
                self.assertFalse(is_current(payload, self.slot, self.now))

    def test_fresh_generation_cannot_renew_expired_report_date(self):
        payload = ready_payload()
        payload["generated_at"] = "2026-10-09T09:00:00+08:00"
        payload["decision_summary"]["valid_until"] = "2026-10-09T22:30:00+08:00"
        self.assertFalse(is_current(payload, self.slot, taipei(2026, 10, 9, 9)))

    def test_saved_summary_must_match_current_deadline_and_readiness(self):
        for mutation in ("missing", "paused", "old_deadline"):
            payload = ready_payload()
            if mutation == "missing":
                payload.pop("decision_summary")
            elif mutation == "paused":
                payload["decision_summary"]["status"] = "paused"
            else:
                payload["decision_summary"]["valid_until"] = "2026-10-09T08:45:00+08:00"
            with self.subTest(mutation=mutation):
                self.assertFalse(is_current(payload, self.slot, self.now))


class RunControlTests(unittest.TestCase):
    def harness(self, latest=None, close=None, state=None, calendar=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        files = {"latest.json": latest or {}, "close-latest.json": close or {}, "notification_state.json": state or {}}
        config = SimpleNamespace(root=Path("virtual-schedule-test"), sources={})
        stack.enter_context(patch.object(scheduled, "load_config", return_value=config))
        stack.enter_context(patch.object(scheduled, "HttpClient"))
        stack.enter_context(patch.object(scheduled, "is_taiwan_trading_day", side_effect=lambda day, *_: calendar(day) if calendar else day.weekday() < 5))
        stack.enter_context(patch.object(scheduled, "load_json", side_effect=lambda path, default=None: copy.deepcopy(files.get(Path(path).name, default))))
        cli = stack.enter_context(patch.object(scheduled.cli, "main", return_value=0))
        notify = stack.enter_context(patch.object(scheduled, "send_line", return_value=True))
        return files, cli, notify

    def test_already_current_skips_build_and_backup_notification(self):
        _, cli, notify = self.harness(latest=ready_payload())
        result = scheduled.run(now=taipei(2026, 10, 8, 9))
        self.assertTrue(result["publish"])  # Recover a previous Pages deployment failure.
        self.assertFalse(result["changed"])
        cli.assert_not_called()
        notify.assert_not_called()

    def test_saved_slot_notification_is_not_repeated_even_with_new_digest(self):
        _, cli, notify = self.harness(latest=ready_payload(), state={"2026-10-08:premarket": "old-digest"})
        result = scheduled.run(notify=True, now=taipei(2026, 10, 8, 9))
        self.assertFalse(result["notified"])
        cli.assert_not_called()
        notify.assert_not_called()

    def test_primary_can_notify_backup_built_report_once_without_rebuilding(self):
        _, cli, notify = self.harness(latest=ready_payload())
        result = scheduled.run(notify=True, now=taipei(2026, 10, 8, 9))
        self.assertTrue(result["publish"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["notified"])
        cli.assert_not_called()
        notify.assert_called_once()

    def test_newer_slot_is_never_overwritten_even_when_forced(self):
        _, cli, notify = self.harness(latest=ready_payload("close"))
        with self.assertRaisesRegex(ValueError, "newer report"):
            scheduled.run(force=True, notify=True, now=taipei(2026, 10, 8, 9))
        cli.assert_not_called()
        notify.assert_not_called()

    def test_fixture_or_old_requested_mode_is_rejected(self):
        _, cli, notify = self.harness()
        for mode in ("fixture", "close"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "out-of-order"):
                scheduled.run(requested_mode=mode, now=taipei(2026, 10, 8, 9))
        cli.assert_not_called()
        notify.assert_not_called()

    def test_expired_holiday_slot_does_not_attempt_or_notify(self):
        calendar = lambda day: day.weekday() < 5 and day != date(2026, 10, 9)
        _, cli, notify = self.harness(calendar=calendar)
        result = scheduled.run(force=True, notify=True, now=taipei(2026, 10, 9, 12))
        self.assertFalse(result["publish"])
        self.assertFalse(result["changed"])
        cli.assert_not_called()
        notify.assert_not_called()

    def test_premarket_build_repairs_previous_close_first_without_notifications(self):
        files, cli, notify = self.harness(close=ready_payload("close", day=date(2026, 10, 6)))

        def build(args):
            if args[0] == "run":
                mode = args[args.index("--mode") + 1]
                day = date.fromisoformat(args[args.index("--date") + 1])
                payload = ready_payload(mode, day=day)
                files["latest.json"] = payload
                if mode == "close":
                    files["close-latest.json"] = payload
            return 0

        cli.side_effect = build
        result = scheduled.run(now=taipei(2026, 10, 8, 9))
        calls = [call.args[0] for call in cli.call_args_list]
        self.assertTrue(result["publish"])
        self.assertEqual([args[0] for args in calls], ["run", "backtest", "run"])
        self.assertEqual(calls[0][1:5], ["--mode", "close", "--date", "2026-10-07"])
        self.assertEqual(calls[2][1:5], ["--mode", "premarket", "--date", "2026-10-08"])
        self.assertTrue(all("--notify" not in args for args in calls))
        notify.assert_not_called()

    def test_valid_previous_close_skips_prerequisite_fetch(self):
        files, cli, notify = self.harness(close=ready_payload("close", day=date(2026, 10, 7)))

        def build(args):
            if args[0] == "run":
                files["latest.json"] = ready_payload()
            return 0

        cli.side_effect = build
        result = scheduled.run(now=taipei(2026, 10, 8, 9))
        self.assertTrue(result["publish"])
        self.assertEqual([call.args[0][0] for call in cli.call_args_list], ["backtest", "run"])
        notify.assert_not_called()

    def test_missing_prerequisite_stops_before_premarket_or_notification(self):
        _, cli, notify = self.harness()
        with self.assertRaisesRegex(RuntimeError, "Previous close is not complete"):
            scheduled.run(notify=True, now=taipei(2026, 10, 8, 9))
        cli.assert_called_once()
        self.assertEqual(cli.call_args.args[0][1:5], ["--mode", "close", "--date", "2026-10-07"])
        notify.assert_not_called()

    def test_zero_exit_without_new_usable_report_cannot_publish(self):
        _, cli, notify = self.harness()
        with self.assertRaisesRegex(RuntimeError, "no deployment permitted"):
            scheduled.run(notify=True, now=taipei(2026, 10, 8, 22))
        self.assertEqual([call.args[0][0] for call in cli.call_args_list], ["backtest", "run"])
        notify.assert_not_called()

    def test_force_refresh_cannot_publish_if_cli_keeps_same_generation(self):
        _, cli, notify = self.harness(latest=ready_payload("close"))
        with self.assertRaisesRegex(RuntimeError, "no deployment permitted"):
            scheduled.run(force=True, notify=True, now=taipei(2026, 10, 8, 22))
        self.assertEqual([call.args[0][0] for call in cli.call_args_list], ["backtest", "run"])
        notify.assert_not_called()

    def test_generation_crossing_into_a_new_slot_is_not_published(self):
        files, cli, notify = self.harness()
        started = taipei(2026, 10, 8, 8, 6)
        finished = taipei(2026, 10, 8, 8, 10)

        def build(args):
            if args[0] == "run":
                files["latest.json"] = ready_payload("close", day=date(2026, 10, 7), generated=finished)
            return 0

        cli.side_effect = build
        with patch.object(scheduled, "datetime", wraps=datetime) as clock:
            clock.now.side_effect = [started, finished]
            with self.assertRaisesRegex(RuntimeError, "slot changed during generation"):
                scheduled.run(notify=True)
        notify.assert_not_called()

    def test_fixture_output_from_successful_cli_still_cannot_publish(self):
        files, cli, notify = self.harness()

        def build(args):
            if args[0] == "run":
                payload = ready_payload("close")
                payload["source_status"][0]["status"] = "fixture"
                files["latest.json"] = payload
            return 0

        cli.side_effect = build
        with self.assertRaisesRegex(RuntimeError, "no deployment permitted"):
            scheduled.run(notify=True, now=taipei(2026, 10, 8, 22))
        notify.assert_not_called()

    def test_existing_monday_premarket_accepts_official_prior_thursday_close(self):
        calendar = lambda day: day.weekday() < 5 and day != date(2026, 10, 9)
        payload = ready_payload(day=date(2026, 10, 12), core_day=date(2026, 10, 8))
        _, cli, notify = self.harness(latest=payload, calendar=calendar)
        result = scheduled.run(now=taipei(2026, 10, 12, 9))
        self.assertTrue(result["publish"])
        self.assertFalse(result["changed"])
        cli.assert_not_called()
        notify.assert_not_called()

    def test_failed_validation_gate_does_not_build_report(self):
        _, cli, notify = self.harness()
        cli.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "validation gate failed"):
            scheduled.run(notify=True, now=taipei(2026, 10, 8, 22))
        cli.assert_called_once()
        self.assertEqual(cli.call_args.args[0][0], "backtest")
        notify.assert_not_called()

    def test_after_midnight_build_targets_previous_close_explicitly(self):
        files, cli, notify = self.harness()

        def build(args):
            if args[0] == "run":
                files["latest.json"] = ready_payload("close", day=date(2026, 10, 8))
            return 0

        cli.side_effect = build
        result = scheduled.run(now=taipei(2026, 10, 9, 0, 10))
        self.assertTrue(result["publish"])
        self.assertEqual(result["date"], "2026-10-08")
        self.assertEqual(cli.call_args.args[0][1:5], ["--mode", "close", "--date", "2026-10-08"])
        notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
