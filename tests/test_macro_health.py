import unittest

import pandas as pd

from src.indicators.macro import MacroAnalyzer


class FakeFetcher:
    def __init__(self):
        self.calls = {"fed": 0, "yields": 0, "vix": 0, "spy": 0}
        index = pd.date_range("2026-01-01", periods=260, freq="B")
        self.series = pd.Series(range(260), index=index, dtype=float)
        self.frame = pd.DataFrame({"Close": range(100, 360)}, index=index)

    def get_fed_funds_rate(self):
        self.calls["fed"] += 1
        return self.series

    def get_treasury_yields(self):
        self.calls["yields"] += 1
        return {"2y": self.series, "10y": self.series + 0.5}

    def get_vix(self):
        self.calls["vix"] += 1
        return self.frame

    def get_market_index(self, ticker, period):
        self.calls["spy"] += 1
        return self.frame


class MacroHealthTests(unittest.TestCase):
    def test_sources_are_loaded_once_and_report_freshness(self):
        fetcher = FakeFetcher()
        analyzer = MacroAnalyzer(fetcher)

        analyzer.get_all_scores()
        health = analyzer.get_data_health()

        self.assertEqual(fetcher.calls, {"fed": 1, "yields": 1, "vix": 1, "spy": 1})
        self.assertEqual(set(health), {"fred_funds", "treasury_yields", "vix", "spy"})
        self.assertTrue(all(details["status"] == "ok" for details in health.values()))
        self.assertTrue(all(details["as_of"] for details in health.values()))

    def test_missing_required_treasury_series_is_partial(self):
        fetcher = FakeFetcher()
        fetcher.get_treasury_yields = lambda: {
            "2y": pd.Series(dtype=float),
            "10y": fetcher.series,
        }
        analyzer = MacroAnalyzer(fetcher)

        analyzer.get_all_scores()
        health = analyzer.get_data_health()

        self.assertEqual(health["treasury_yields"]["status"], "partial")
        self.assertIn("2y", health["treasury_yields"]["message"])

    def test_treasury_health_uses_oldest_required_series_date(self):
        fetcher = FakeFetcher()
        stale = fetcher.series.loc[:"2026-03-01"]
        fetcher.get_treasury_yields = lambda: {
            "2y": stale,
            "10y": fetcher.series,
        }
        analyzer = MacroAnalyzer(fetcher)

        analyzer.get_all_scores()
        health = analyzer.get_data_health()["treasury_yields"]

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["as_of"], stale.index[-1].isoformat())

    def test_short_spy_history_is_partial(self):
        fetcher = FakeFetcher()
        fetcher.get_market_index = lambda ticker, period: fetcher.frame.tail(20)
        analyzer = MacroAnalyzer(fetcher)

        analyzer.get_all_scores()
        health = analyzer.get_data_health()["spy"]

        self.assertEqual(health["status"], "partial")


if __name__ == "__main__":
    unittest.main()
