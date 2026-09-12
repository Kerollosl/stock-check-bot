import copy
import unittest
from datetime import datetime, timezone

from src.opportunity_engine import evaluate_candidate


NOW = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)


def candidate_snapshot():
    """Synthetic USD company; figures are deliberately not a real stock."""
    def flow(period, cfo, capex, sbc):
        return {"date": period, "operating_cash_flow": cfo, "capital_expenditure": capex,
                "free_cash_flow": cfo - abs(capex), "stock_based_compensation": sbc}
    return {
        "ticker": "DEMO", "fetched_at": NOW.isoformat(), "quote_as_of": "2026-09-11T20:00:00Z",
        "info": {"symbol": "DEMO", "longName": "Demonstration Industries", "quoteType": "EQUITY",
                 "currency": "USD", "financialCurrency": "USD", "exchange": "NYQ", "sector": "Industrials",
                 "industry": "Specialty Industrial Machinery", "regularMarketPrice": 80,
                 "marketCap": 80e9, "sharesOutstanding": 1e9, "averageVolume": 1e6,
                 "profitMargins": .15, "operatingMargins": .20, "revenueGrowth": .06,
                 "totalDebt": 15e9, "totalCash": 5e9, "ebitda": 12e9, "fiftyTwoWeekHigh": 120},
        "annual_cashflows": [flow(f"{year}-12-31", 14e9, -3e9, 1e9) for year in (2025, 2024, 2023)],
        "quarterly_cashflows": [flow(period, 3.5e9, -.75e9, .25e9) for period in
                                ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")],
        "history": [], "news": [], "errors": [],
    }


class OpportunityEngineTests(unittest.TestCase):
    def evaluate(self, snapshot=None, **kwargs):
        return evaluate_candidate(snapshot or candidate_snapshot(), now=NOW, **kwargs)

    def test_quality_at_discount_is_research_candidate(self):
        card = self.evaluate()
        self.assertEqual(card["status"], "candidate")
        self.assertAlmostEqual(card["metrics"]["drawdown_pct"], 100 / 3)
        self.assertIn("unverified", card["why_now"])
        self.assertNotIn("confidence", card)
        self.assertIn("10-K/10-Q", card["next_step"])

    def test_cash_arithmetic_deducts_capex_and_stock_compensation(self):
        card = self.evaluate()
        self.assertEqual(card["metrics"]["ttm_owner_cash_flow"], 10e9)
        self.assertEqual(card["metrics"]["normalised_annual_owner_cash_flow"], 10e9)
        self.assertEqual(card["metrics"]["free_cash_flow_yield_pct"], 12.5)
        self.assertEqual(card["valuation"]["cash_flow_per_share"], 10)

    def test_equity_cash_flow_does_not_double_subtract_debt(self):
        snapshot = candidate_snapshot()
        baseline = self.evaluate(snapshot)
        snapshot["info"]["totalDebt"] = 35e9
        leveraged = self.evaluate(snapshot)
        self.assertEqual(leveraged["valuation"]["base"], baseline["valuation"]["base"])
        self.assertGreater(leveraged["metrics"]["net_debt_to_ebitda"], baseline["metrics"]["net_debt_to_ebitda"])

    def test_discount_math_and_stress_are_explicit(self):
        card = self.evaluate()
        growth = .06
        base = sum(10 * (1 + growth) ** year / 1.1 ** year for year in range(1, 6))
        base += 10 * 1.06 ** 5 * 1.02 / (.10 - .02) / 1.1 ** 5
        self.assertAlmostEqual(card["metrics"]["margin_of_safety_pct"], (1 - 80 / base) * 100)
        self.assertEqual(card["valuation"]["entry_price"], round(base * .75, 2))
        self.assertAlmostEqual(card["valuation"]["low"], 8 / .12, places=2)
        self.assertLessEqual(card["valuation"]["stress_downside_pct"], 35)

    def test_quality_without_discount_is_watch(self):
        snapshot = candidate_snapshot()
        snapshot["info"].update(regularMarketPrice=115, marketCap=115e9)
        self.assertEqual(self.evaluate(snapshot)["status"], "watch")

    def test_deep_drawdown_with_losses_is_value_trap_not_candidate(self):
        snapshot = candidate_snapshot()
        snapshot["info"].update(regularMarketPrice=40, marketCap=40e9, profitMargins=-.1, revenueGrowth=-.12)
        card = self.evaluate(snapshot)
        self.assertEqual(card["status"], "rejected")
        self.assertEqual(card["rank"], 0)

    def test_missing_sbc_is_not_imputed_as_zero(self):
        snapshot = candidate_snapshot()
        snapshot["quarterly_cashflows"][0]["stock_based_compensation"] = None
        card = self.evaluate(snapshot)
        self.assertEqual(card["status"], "incomplete")
        self.assertEqual(card["valuation"], {})

    def test_nan_fundamentals_and_unknown_debt_block(self):
        for key, value in (("revenueGrowth", float("nan")), ("totalDebt", None), ("ebitda", float("inf"))):
            with self.subTest(key=key):
                snapshot = candidate_snapshot()
                snapshot["info"][key] = value
                self.assertEqual(self.evaluate(snapshot)["status"], "incomplete")

    def test_old_fiscal_period_is_incomplete(self):
        snapshot = candidate_snapshot()
        for item, period in zip(snapshot["quarterly_cashflows"], ("2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31")):
            item["date"] = period
        self.assertEqual(self.evaluate(snapshot)["status"], "incomplete")

    def test_refreshing_quote_does_not_hide_old_financial_cache(self):
        snapshot = candidate_snapshot()
        snapshot["fundamentals_fetched_at"] = "2026-09-09T20:00:00Z"
        self.assertEqual(self.evaluate(snapshot)["status"], "incomplete")

    def test_stale_quote_and_future_quote_are_incomplete(self):
        for quote_time in ("2026-09-10T20:00:00Z", "2026-09-12T20:00:00Z", "2026-09-11T14:00:00Z"):
            snapshot = candidate_snapshot()
            snapshot["quote_as_of"] = quote_time
            self.assertEqual(self.evaluate(snapshot)["status"], "incomplete")

    def test_intraday_quote_must_be_recent(self):
        snapshot = candidate_snapshot()
        now = datetime(2026, 9, 11, 17, tzinfo=timezone.utc)
        snapshot["fetched_at"] = now.isoformat()
        snapshot["quote_as_of"] = "2026-09-11T16:40:00Z"
        self.assertEqual(evaluate_candidate(snapshot, now=now)["status"], "candidate")
        snapshot["quote_as_of"] = "2026-09-10T20:00:00Z"
        self.assertEqual(evaluate_candidate(snapshot, now=now)["status"], "incomplete")

    def test_weekend_uses_last_completed_session(self):
        snapshot = candidate_snapshot()
        now = datetime(2026, 9, 12, 15, tzinfo=timezone.utc)
        snapshot["fetched_at"] = now.isoformat()
        self.assertEqual(evaluate_candidate(snapshot, now=now)["status"], "candidate")

    def test_noncontiguous_or_duplicate_quarters_do_not_form_ttm(self):
        for kind in ("gap", "duplicate", "conflicting_duplicate"):
            snapshot = candidate_snapshot()
            if kind == "gap":
                snapshot["quarterly_cashflows"][-1]["date"] = "2025-03-31"
            elif kind == "duplicate":
                snapshot["quarterly_cashflows"][-1] = copy.deepcopy(snapshot["quarterly_cashflows"][0])
            else:
                conflict = copy.deepcopy(snapshot["quarterly_cashflows"][0])
                conflict["operating_cash_flow"] = 50e9
                snapshot["quarterly_cashflows"].append(conflict)
            with self.subTest(kind=kind):
                self.assertEqual(self.evaluate(snapshot)["status"], "incomplete")

    def test_boom_is_not_extrapolated(self):
        snapshot = candidate_snapshot()
        for item in snapshot["quarterly_cashflows"]:
            item["operating_cash_flow"] = 10e9
        snapshot["info"]["revenueGrowth"] = 2
        card = self.evaluate(snapshot)
        self.assertEqual(card["valuation"]["normalised_annual_owner_cash_flow"], 10e9)
        self.assertEqual(card["valuation"]["growth_assumption_pct"], 8)

    def test_sustained_cash_flow_and_weak_balance_sheet_gate(self):
        for kind in ("negative_year", "high_debt", "declining_cash"):
            snapshot = candidate_snapshot()
            if kind == "negative_year":
                snapshot["annual_cashflows"][1]["operating_cash_flow"] = 2e9
            elif kind == "high_debt":
                snapshot["info"]["totalDebt"] = 90e9
            else:
                for item in snapshot["quarterly_cashflows"]:
                    item["operating_cash_flow"] = 2.5e9
            with self.subTest(kind=kind):
                self.assertEqual(self.evaluate(snapshot)["status"], "rejected")

    def test_currency_and_financial_sector_are_excluded(self):
        for update in ({"financialCurrency": "JPY"}, {"currency": "EUR"}, {"industry": "Banks - Regional", "sector": "Financial Services"}, {"industry": "REIT - Retail", "sector": "Real Estate"}):
            snapshot = candidate_snapshot()
            snapshot["info"].update(update)
            self.assertEqual(self.evaluate(snapshot)["status"], "rejected")

    def test_split_share_scale_mismatch_blocks_valuation(self):
        snapshot = candidate_snapshot()
        snapshot["info"]["sharesOutstanding"] /= 10
        card = self.evaluate(snapshot)
        self.assertEqual(card["status"], "incomplete")
        self.assertEqual(card["valuation"], {})

    def test_split_history_high_mismatch_blocks_candidate(self):
        from datetime import timedelta
        snapshot = candidate_snapshot()
        snapshot["history_adjusted"] = True
        snapshot["history"] = [{"date": (NOW.date() - timedelta(days=day)).isoformat(), "high": 1200, "close": 1000, "volume": 1e6} for day in range(220)]
        self.assertEqual(self.evaluate(snapshot)["status"], "incomplete")

    def test_configurable_thresholds_are_honored(self):
        self.assertEqual(self.evaluate(config={"min_drawdown_pct": 40})["status"], "watch")
        self.assertEqual(self.evaluate(config={"opportunity": {"max_stress_downside_pct": 1}})["status"], "watch")

    def test_input_is_not_mutated_and_output_is_json_safe(self):
        import json
        snapshot = candidate_snapshot()
        before = copy.deepcopy(snapshot)
        json.dumps(self.evaluate(snapshot), allow_nan=False)
        self.assertEqual(snapshot, before)


if __name__ == "__main__":
    unittest.main()
