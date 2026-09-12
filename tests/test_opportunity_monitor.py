import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from src.opportunity_brief import render_html
from src.opportunity_delivery import deliver, email_message
from src.opportunity_monitor import (
    cli, empty_state, market_scan_due, notification_plan, read_state,
    record_delivery, run_scan, select_quotes,
)


NOW = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)


def idea(ticker="TEST", status="candidate", price=80):
    return {"ticker": ticker, "name": "Test operating business", "sector": "Industrials",
            "price": price, "quote_as_of": NOW.isoformat(), "status": status,
            "rank": 40, "metrics": {"margin_of_safety_pct": 30, "drawdown_pct": 25},
            "valuation": {"low": 65, "base": 115, "high": 140, "research_price": 86, "assumptions": []},
            "why_now": "Cleared the price rules", "quality_summary": "Positive cash flows",
            "valuation_summary": "Assumed model value", "risks": ["Revenue could decline"],
            "first_rejection": "Revenue declined" if status == "rejected" else None,
            "next_step": "Read the latest report", "news": [], "sources": [],
            "quality_checks": [{"name": "Fresh quote", "passed": True}]}


def report(items=None, mode="scan"):
    items = [idea()] if items is None else items
    return {"items": items, "mode": mode, "status": "ok", "generated_at": NOW.isoformat(),
            "counts": {"screened": 750, "evaluated": len(items), "candidates": sum(x["status"] == "candidate" for x in items)},
            "universe": {"source": "Test fixture"}, "scan_issues": [],
            "notification": {"required": True, "kind": "opportunity", "tickers": [x["ticker"] for x in items], "week": "2026-W37"}}


class NotificationTests(unittest.TestCase):
    def test_new_candidate_then_unchanged_is_quiet(self):
        state = empty_state()
        current = report()
        current["notification"] = notification_plan(current, state, {}, NOW)
        self.assertTrue(current["notification"]["required"])
        self.assertEqual(state["alerts"], {})
        record_delivery(state, current, NOW)
        self.assertFalse(notification_plan(current, state, {}, NOW + timedelta(days=1))["required"])

    def test_more_discount_requires_both_cooldown_and_price_change(self):
        state = empty_state()
        record_delivery(state, report(), NOW)
        cheaper = report([idea(price=72)])
        self.assertFalse(notification_plan(cheaper, state, {}, NOW + timedelta(hours=24))["required"])
        later = notification_plan(cheaper, state, {}, NOW + timedelta(hours=49))
        self.assertTrue(later["required"])
        self.assertEqual(later["previous_alerts"]["TEST"], NOW.isoformat())

    def test_deterioration_notifies_once_but_missing_data_is_not_a_thesis_break(self):
        state = empty_state()
        record_delivery(state, report(), NOW)
        self.assertFalse(notification_plan(report([idea(status="incomplete")]), state, {}, NOW)["required"])
        current = report([idea(status="rejected")])
        current["notification"] = notification_plan(current, state, {}, NOW)
        self.assertEqual(current["notification"]["kind"], "thesis_change")
        record_delivery(state, current, NOW)
        self.assertFalse(notification_plan(current, state, {}, NOW)["required"])

    def test_weekly_roundup_once_per_week_even_without_opportunities(self):
        state = empty_state()
        current = report([], mode="weekly")
        current["notification"] = notification_plan(current, state, {}, NOW)
        self.assertTrue(current["notification"]["required"])
        record_delivery(state, current, NOW)
        self.assertFalse(notification_plan(current, state, {}, NOW)["required"])
        self.assertTrue(notification_plan(current, state, {}, NOW + timedelta(days=7))["required"])

    def test_bad_scan_or_corrupt_ledger_cannot_send_stock_ideas(self):
        current = report()
        current["status"] = "degraded"
        self.assertFalse(notification_plan(current, empty_state(), {}, NOW)["required"])
        current["status"] = "ok"
        current["state_warning"] = "corrupted ledger"
        self.assertFalse(notification_plan(current, empty_state(), {}, NOW)["required"])


class ScanTests(unittest.TestCase):
    def test_rotation_retains_discovery_capacity_and_tracks_rebounded_ideas(self):
        state = empty_state()
        state["alerts"] = {f"T{x}": {} for x in range(10)}
        quotes = [{"symbol": f"T{x}", "regularMarketPrice": 95, "fiftyTwoWeekHigh": 100, "marketCap": 1e10} for x in range(10)]
        quotes += [{"symbol": "NEW", "regularMarketPrice": 70, "fiftyTwoWeekHigh": 100, "marketCap": 9e9}]
        selected, count = select_quotes(quotes, [], state, {"max_deep_checks": 3})
        self.assertEqual(count, 11)
        self.assertEqual(len(selected), 3)
        self.assertIn("NEW", [x["symbol"] for x in selected])

    def test_ineligible_and_invalid_prices_do_not_enter_the_research_queue(self):
        quotes = [{"symbol": "HIGH", "regularMarketPrice": 99, "fiftyTwoWeekHigh": 100},
                  {"symbol": "BAD", "regularMarketPrice": float("nan"), "fiftyTwoWeekHigh": 100},
                  {"symbol": "CHEAP", "regularMarketPrice": 70, "fiftyTwoWeekHigh": 100}]
        selected, count = select_quotes(quotes, ["PERSONAL"], empty_state(), {})
        self.assertEqual({x["symbol"] for x in selected}, {"PERSONAL", "CHEAP"})
        self.assertEqual(count, 2)

    def test_tracked_idea_outside_current_universe_is_still_checked(self):
        state = empty_state()
        state['alerts']['COLLAPSED'] = {'status': 'candidate'}
        selected, count = select_quotes([], [], state, {})
        self.assertEqual(selected, [{'symbol': 'COLLAPSED'}])

    def test_market_calendar_handles_holiday_dst_and_early_close(self):
        self.assertFalse(market_scan_due(datetime(2026, 9, 7, 16, tzinfo=timezone.utc)))
        self.assertTrue(market_scan_due(NOW))
        self.assertTrue(market_scan_due(datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)))
        self.assertFalse(market_scan_due(datetime(2026, 11, 27, 19, tzinfo=timezone.utc)))

    def test_total_data_failure_is_not_reported_as_no_bargains(self):
        def fake_fetch(ticker, **kwargs):
            return {"info": {"symbol": ticker}, "errors": ["provider unavailable"]}
        current = run_scan({}, empty_state(), ["TEST"], now=NOW,
                           universe={"quotes": [{"symbol": "TEST"}], "complete": True}, snapshot_fetcher=fake_fetch)
        self.assertEqual(current["status"], "degraded")
        self.assertFalse(current["notification"]["required"])

    def test_successful_delivery_is_required_to_advance_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text("watchlist: [TEST]\nopportunity: {}\n")
            state_path = Path(directory) / "state.json"
            with patch("src.opportunity_monitor.run_scan", return_value=report()), patch("src.opportunity_monitor.deliver", side_effect=RuntimeError("delivery refused")):
                result = CliRunner().invoke(cli, ["--config", str(config), "--state", str(state_path), "--output-dir", str(Path(directory) / "report"), "--send"])
            self.assertEqual(result.exit_code, 3, result.output)
            self.assertEqual(json.loads(state_path.read_text())["alerts"], {})

    def test_corrupt_state_remains_intact_for_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"version":2,"snapshots":[],"alerts":{},"journal":[]}')
            state, warning = read_state(path)
            self.assertIsNotNone(warning)
            self.assertEqual(state, empty_state())
            self.assertIn('"snapshots":[]', path.read_text())

    def test_pending_weekly_delivery_retries_during_ordinary_closed_market_run(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yaml'
            config.write_text('watchlist: [TEST]\nopportunity: {}\n')
            state_path = Path(directory) / 'state.json'
            state = empty_state()
            pending = report(mode='weekly')
            pending['notification']['kind'] = 'weekly'
            state['pending_delivery'] = pending
            state_path.write_text(json.dumps(state))
            with patch('src.opportunity_monitor.market_scan_due', return_value=False), patch('src.opportunity_monitor.run_scan') as scan, patch('src.opportunity_monitor.deliver', return_value={'status':'accepted_by_mail_server'}):
                result = CliRunner().invoke(cli, ['--config', str(config), '--state', str(state_path), '--output-dir', str(Path(directory) / 'out'), '--scheduled', '--send'])
            self.assertEqual(result.exit_code, 0, result.output)
            scan.assert_not_called()
            saved = json.loads(state_path.read_text())
            self.assertIsNone(saved['pending_delivery'])
            self.assertEqual(saved['last_weekly'], '2026-W37')


class BriefTests(unittest.TestCase):
    def test_thesis_change_does_not_present_rejected_stock_as_qualifying(self):
        current = report([idea(status='rejected')])
        current['notification']['kind'] = 'thesis_change'
        html = render_html(current)
        self.assertIn('What changed.', html)
        self.assertIn('Revenue declined', html)
        self.assertNotIn('Why the business passed', html)
        self.assertNotIn('Research zone:', html)

    def test_html_escapes_upstream_text_and_drops_unsafe_links(self):
        item = idea()
        item["name"] = '<img src=x onerror="alert(1)">'
        item["sources"] = [{"label": "Bad source", "url": "javascript:alert(1)"}]
        html = render_html(report([item]))
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;img', html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn("not an instruction to buy", html)

    def test_message_has_html_and_plain_text_and_rejects_header_injection(self):
        message = email_message(report(), "sender@example.com", "reader@example.com")
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        self.assertEqual([x.get_content_type() for x in message.iter_parts()], ["text/plain", "text/html"])
        with self.assertRaises(ValueError):
            email_message(report(), "sender@example.com", "reader@example.com\nBcc:evil@example.com")

    def test_configured_mail_failure_does_not_silently_fall_back(self):
        with patch.dict(os.environ, {"SMTP_PASSWORD": "test-password"}), patch("src.opportunity_delivery.deliver_email", side_effect=RuntimeError("bad auth")), patch("src.opportunity_delivery.deliver_github") as fallback:
            with self.assertRaises(RuntimeError):
                deliver(report(), "owner/repo")
            fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
