import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from src.reporting import (
    _apply_source_freshness,
    compare_reports,
    latest_completed_market_session,
    load_previous_report,
    render_markdown,
)


def stock(ticker="MSFT", score=0.50, signal="HOLD", dips=None):
    return {
        "ticker": ticker,
        "status": "ok",
        "as_of": "2026-09-08T16:00:00-04:00",
        "price": 100.0,
        "change_pct": 1.0,
        "composite_score": score,
        "signal": signal,
        "confidence": "LOW",
        "dips": dips
        or {
            "daily_dip": False,
            "weekly_dip": False,
            "major_dip_from_high": False,
        },
    }


def report(stocks=None, status="ok", errors=None):
    return {
        "generated_at": "2026-09-08T21:00:00+00:00",
        "status": status,
        "macro": {"overall": 0.5},
        "sources": {
            "spy": {"status": "ok", "as_of": "2026-09-08T16:00:00-04:00"}
        },
        "stocks": stocks or [stock()],
        "warnings": [],
        "errors": errors or [],
    }


class CompareReportsTests(unittest.TestCase):
    def test_healthy_baseline_does_not_notify(self):
        comparison = compare_reports(report(), None)

        self.assertTrue(comparison["baseline"])
        self.assertFalse(comparison["notification_required"])
        self.assertEqual(comparison["events"], [])

    def test_signal_change_and_large_score_move_notify(self):
        previous = report([stock(score=0.50, signal="HOLD")])
        current = report([stock(score=0.60, signal="BUY")])

        comparison = compare_reports(current, previous)
        event_types = {event["type"] for event in comparison["events"]}

        self.assertTrue(comparison["notification_required"])
        self.assertEqual(event_types, {"signal_changed", "score_moved"})

    def test_new_dip_notifies(self):
        previous = report()
        current_dips = {
            "daily_dip": True,
            "weekly_dip": False,
            "major_dip_from_high": False,
        }
        current = report([stock(dips=current_dips)])

        comparison = compare_reports(current, previous)

        self.assertEqual(comparison["events"][0]["type"], "dip_started")

    def test_new_dip_notifies_when_fundamentals_are_incomplete(self):
        previous_stock = stock()
        previous_stock["status"] = "warning"
        current_stock = stock(
            dips={
                "daily_dip": True,
                "weekly_dip": False,
                "major_dip_from_high": False,
            }
        )
        current_stock["status"] = "warning"

        comparison = compare_reports(
            report([current_stock], status="warning"),
            report([previous_stock], status="warning"),
        )

        self.assertEqual(
            [event["type"] for event in comparison["events"]],
            ["dip_started"],
        )

    def test_degraded_report_notifies_even_without_previous_report(self):
        current = report(
            status="degraded",
            errors=[{"scope": "fred_funds", "message": "No data returned"}],
        )

        comparison = compare_reports(current, None)

        self.assertTrue(comparison["notification_required"])
        self.assertEqual(comparison["events"][0]["type"], "data_error")

    def test_repeated_identical_data_error_does_not_notify(self):
        error = {"scope": "fred_funds", "message": "No data returned"}
        previous = report(status="degraded", errors=[error])
        current = report(status="degraded", errors=[error])

        comparison = compare_reports(current, previous)

        self.assertFalse(comparison["notification_required"])
        self.assertEqual(comparison["events"], [])

    def test_ticker_data_recovery_notifies_without_score_noise(self):
        previous_stock = {
            "ticker": "MSFT",
            "status": "error",
            "error": "Price fetch failed",
        }
        previous = report([previous_stock], status="degraded")
        current = report([stock(score=0.70, signal="STRONG BUY")])

        comparison = compare_reports(current, previous)

        self.assertEqual(
            [event["type"] for event in comparison["events"]],
            ["ticker_data_recovered"],
        )

    def test_corrupt_previous_report_resets_baseline_with_warning(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "previous.json"
            path.write_text("not valid json")

            previous, warning = load_previous_report(str(path))

        self.assertIsNone(previous)
        self.assertIn("could not be read", warning)

    def test_wrong_shaped_previous_report_resets_baseline(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "previous.json"
            path.write_text("[]")

            previous, warning = load_previous_report(str(path))

        self.assertIsNone(previous)
        self.assertIn("invalid schema", warning)

    def test_market_calendar_handles_us_holiday(self):
        labor_day_evening = datetime(2026, 9, 7, 22, tzinfo=timezone.utc)

        session = latest_completed_market_session(labor_day_evening)

        self.assertEqual(session, date(2026, 9, 4))

    def test_stale_market_source_is_marked(self):
        sources = {
            "spy": {"status": "ok", "as_of": "2026-09-03T16:00:00-04:00"},
            "vix": {"status": "ok", "as_of": "2026-09-04T16:00:00-04:00"},
        }

        _apply_source_freshness(
            sources,
            expected_market_session=date(2026, 9, 4),
            generated_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        )

        self.assertEqual(sources["spy"]["status"], "stale")
        self.assertEqual(sources["vix"]["status"], "ok")

    def test_markdown_contains_core_sections(self):
        current = report()
        comparison = compare_reports(current, None)

        markdown = render_markdown(current, comparison)

        self.assertIn("# Stock Check", markdown)
        self.assertIn("| MSFT |", markdown)
        self.assertIn("Initial baseline created", markdown)
        self.assertIn("not financial advice", markdown)


if __name__ == "__main__":
    unittest.main()
