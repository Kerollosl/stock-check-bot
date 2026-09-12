import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from src.opportunity_data import (
    _can_reuse_fundamentals,
    discover_universe,
    fetch_snapshot,
    normalize_cashflows,
    normalize_history,
    normalize_news,
)


NOW = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)


def quote(symbol="ACME", cap=10_000_000_000, price=100):
    return {
        "symbol": symbol, "marketCap": cap, "regularMarketPrice": price,
        "regularMarketTime": int((NOW - timedelta(minutes=1)).timestamp()),
        "regularMarketChangePercent": -1, "averageDailyVolume3Month": 500_000,
        "currency": "USD", "quoteType": "EQUITY", "exchange": "NYQ",
        "fiftyTwoWeekHigh": 150,
    }


def cashflows():
    return pd.DataFrame(
        {pd.Timestamp("2026-06-30"): [100, -20, 80, 10],
         pd.Timestamp("2025-06-30"): [90, -15, 75, float("nan")]},
        index=["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow", "Stock Based Compensation"],
    )


class DiscoveryTests(unittest.TestCase):
    @patch("src.opportunity_data.yf.screen")
    def test_pagination_deduplicates_and_discloses_cap(self, screen):
        screen.side_effect = [
            {"start": 0, "total": 1_200, "quotes": [quote("AAA"), quote("BBB")]},
            {"start": 2, "total": 1_200, "quotes": [quote("BBB"), quote("CCC")]},
        ]
        result = discover_universe({"universe_limit": 4})
        self.assertEqual({q["symbol"] for q in result["quotes"]}, {"AAA", "BBB", "CCC"})
        self.assertEqual(screen.call_args_list[1].kwargs["offset"], 2)
        self.assertTrue(result["complete"])
        self.assertTrue(result["capped"])
        self.assertEqual(result["total_available"], 1_200)

    @patch("src.opportunity_data.yf.screen")
    def test_filters_funds_wrong_currency_and_illiquid_small_companies(self, screen):
        candidates = [quote("AAA"), quote("BBB", cap=1_000), quote("CCC")]
        candidates[2]["quoteType"] = "ETF"
        foreign = quote("DDD")
        foreign["currency"] = "CAD"
        illiquid = quote("EEE")
        illiquid["averageDailyVolume3Month"] = None
        screen.return_value = {"total": 5, "quotes": candidates + [foreign, illiquid]}
        self.assertEqual([q["symbol"] for q in discover_universe()["quotes"]], ["AAA"])

    @patch("src.opportunity_data.yf.screen")
    def test_custom_failure_uses_live_fallback_with_partial_coverage(self, screen):
        screen.side_effect = [
            RuntimeError("POST unavailable"),
            {"total": 100, "quotes": [quote()]},
            RuntimeError("GET unavailable"),
            {"total": 100, "quotes": [quote()]},
            {"total": 100, "quotes": []},
        ]
        result = discover_universe()
        self.assertEqual(len(result["quotes"]), 1)
        self.assertFalse(result["complete"])
        self.assertIn("fallback", result["source"])
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(screen.call_count, 5)

    @patch("src.opportunity_data.yf.screen")
    def test_malformed_empty_response_never_becomes_successful_empty_market(self, screen):
        screen.return_value = {"unexpected": "payload"}
        result = discover_universe()
        self.assertFalse(result["complete"])
        self.assertEqual(result["quotes"], [])
        self.assertTrue(result["errors"])

    @patch("src.opportunity_data.yf.screen")
    def test_repeated_page_stops_and_marks_partial(self, screen):
        screen.side_effect = [
            {"start": 0, "total": 30, "quotes": [quote()]},
            {"start": 1, "total": 30, "quotes": [quote()]},
            *[{"total": 1, "quotes": [quote()]}] * 4,
        ]
        result = discover_universe({"universe_limit": 20})
        self.assertFalse(result["complete"])
        self.assertIn("repeated", result["errors"][0])
        self.assertEqual(screen.call_count, 6)


class NormalizerTests(unittest.TestCase):
    def test_reported_cashflow_periods_preserve_missing_sbc(self):
        result = normalize_cashflows(cashflows())
        self.assertEqual(result[0]["date"], "2026-06-30")
        self.assertEqual(result[0]["capital_expenditure"], -20)
        self.assertIsNone(result[1]["stock_based_compensation"])
        json.dumps(result, allow_nan=False)

    def test_empty_period_not_counted_as_a_reported_year(self):
        frame = cashflows()
        frame[pd.Timestamp("2024-06-30")] = float("nan")
        self.assertEqual(len(normalize_cashflows(frame)), 2)

    def test_history_uses_raw_close_and_exchange_session_dates(self):
        frame = pd.DataFrame(
            {"Close": [100, float("nan")], "Adj Close": [70, 70], "High": [110, 111], "Volume": [1_000, 1_500]},
            index=pd.DatetimeIndex(["2026-09-10", "2026-09-11"], tz="America/New_York"),
        )
        self.assertEqual(normalize_history(frame), [{"date": "2026-09-10", "close": 100, "high": 110, "volume": 1_000}])

    def test_news_rejects_unsafe_old_future_or_undated_links_and_deduplicates(self):
        def story(url="https://example.com/story", published="2026-09-10T17:00:00Z"):
            return {"content": {"title": "Results published", "canonicalUrl": {"url": url}, "pubDate": published, "provider": {"displayName": "Example"}}}
        result = normalize_news([
            story(), story(), story("javascript:alert(1)"),
            story("https://example.com/old", "2026-08-01T00:00:00Z"),
            story("https://example.com/future", "2026-10-01T00:00:00Z"),
            story("https://example.com/undated", None),
        ], now=NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["publisher"], "Example")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.stock = MagicMock()
        self.stock.get_info.return_value = {
            **quote(price=98), "currentPrice": 98, "financialCurrency": "USD",
            "sharesOutstanding": 100_000_000, "operatingMargins": 0.3,
        }
        self.stock.get_cash_flow.return_value = cashflows()
        self.stock.history.return_value = pd.DataFrame(
            {"Close": [100], "High": [101], "Volume": [500_000]},
            index=pd.to_datetime(["2026-09-11"]),
        )
        self.stock.get_news.return_value = []

    @patch("src.opportunity_data.yf.Ticker")
    def test_fresh_screener_price_wins_over_info_and_preserves_financial_shares(self, ticker):
        ticker.return_value = self.stock
        result = fetch_snapshot("ACME", quote(price=100), now=NOW)
        self.assertEqual(result["info"]["currentPrice"], 100)
        self.assertEqual(result["info"]["sharesOutstanding"], 100_000_000)
        self.assertEqual(result["info"]["financialCurrency"], "USD")
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["history_adjusted"])
        self.assertFalse(self.stock.history.call_args.kwargs["auto_adjust"])
        self.assertEqual(self.stock.get_cash_flow.call_count, 2)
        json.dumps(result, allow_nan=False)

    @patch("src.opportunity_data.yf.Ticker")
    def test_cache_reuses_fundamentals_but_updates_quote_and_history(self, ticker):
        ticker.return_value = self.stock
        old = fetch_snapshot("ACME", quote(), now=NOW - timedelta(hours=1))
        self.stock.reset_mock()
        fresh = fetch_snapshot("ACME", quote(price=101), cached=old, now=NOW)
        self.stock.get_info.assert_not_called()
        self.stock.get_cash_flow.assert_not_called()
        self.stock.history.assert_called_once()
        self.assertEqual(fresh["info"]["currentPrice"], 101)
        self.assertEqual(fresh["fundamentals_fetched_at"], old["fundamentals_fetched_at"])

    def test_cache_expires_after_24h_or_earnings_or_sharp_price_move(self):
        cached = {
            "fundamentals_fetched_at": (NOW - timedelta(hours=23)).isoformat(),
            "info": {"currentPrice": 100}, "annual_cashflows": [{}], "quarterly_cashflows": [{}],
        }
        self.assertTrue(_can_reuse_fundamentals(cached, quote(), {}, NOW))
        self.assertFalse(_can_reuse_fundamentals(cached, quote(price=90), {}, NOW))
        self.assertFalse(_can_reuse_fundamentals(cached, {}, {}, NOW))
        self.assertFalse(_can_reuse_fundamentals(cached, quote(), {"fundamental_cache_hours": 48}, NOW + timedelta(hours=2)))
        cached["info"]["earningsTimestamp"] = int((NOW - timedelta(hours=2)).timestamp())
        self.assertFalse(_can_reuse_fundamentals(cached, quote(), {}, NOW))

    @patch("src.opportunity_data.yf.Ticker")
    def test_no_quote_timestamp_is_not_replaced_by_fetch_time(self, ticker):
        ticker.return_value = self.stock
        self.stock.get_info.return_value.pop("regularMarketTime")
        result = fetch_snapshot("ACME", now=NOW)
        self.assertIsNone(result["quote_as_of"])
        self.assertIn("Current quote has no valid source timestamp", result["errors"])

    @patch("src.opportunity_data.yf.Ticker")
    def test_financial_currency_not_inferred_from_quote(self, ticker):
        ticker.return_value = self.stock
        self.stock.get_info.return_value.pop("financialCurrency")
        result = fetch_snapshot("ACME", quote(), now=NOW)
        self.assertNotIn("financialCurrency", result["info"])

    @patch("src.opportunity_data.yf.Ticker")
    def test_per_source_failure_is_contained_and_json_stays_valid(self, ticker):
        ticker.return_value = self.stock
        self.stock.get_cash_flow.side_effect = RuntimeError("unavailable")
        self.stock.get_news.side_effect = RuntimeError("unavailable")
        result = fetch_snapshot("ACME", quote(), now=NOW)
        self.assertEqual(len(result["fundamentals_errors"]), 2)
        self.assertEqual(len(result["errors"]), 3)
        self.assertEqual(result["info"]["currentPrice"], 100)
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
