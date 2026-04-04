import os
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
from fredapi import Fred


class DataFetcher:
    """Centralized data fetching for stock, earnings, and macro data."""

    def __init__(self):
        fred_key = os.getenv("FRED_API_KEY")
        if fred_key and fred_key != "your_fred_api_key_here" and len(fred_key) == 32:
            self.fred = Fred(api_key=fred_key)
        else:
            self.fred = None

    def get_stock_history(self, ticker: str, period: str = "2y") -> pd.DataFrame:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, auto_adjust=True)
        return df

    def get_stock_history_range(
        self, ticker: str, start: str, end: str
    ) -> pd.DataFrame:
        stock = yf.Ticker(ticker)
        df = stock.history(start=start, end=end, auto_adjust=True)
        return df

    def get_earnings(self, ticker: str) -> dict:
        stock = yf.Ticker(ticker)
        info = stock.info
        calendar = {}
        try:
            cal = stock.calendar
            if cal is not None:
                calendar = cal if isinstance(cal, dict) else cal.to_dict()
        except Exception:
            pass

        earnings_hist = pd.DataFrame()
        try:
            earnings_hist = stock.earnings_history
            if earnings_hist is None:
                earnings_hist = pd.DataFrame()
        except Exception:
            pass

        return {
            "info": info,
            "calendar": calendar,
            "earnings_history": earnings_hist,
            "trailing_eps": info.get("trailingEps"),
            "forward_eps": info.get("forwardEps"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg_ratio": info.get("pegRatio"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
        }

    def get_fed_funds_rate(self) -> pd.Series | None:
        if not self.fred:
            return None
        return self.fred.get_series("FEDFUNDS", observation_start="2020-01-01")

    def get_treasury_yields(self) -> dict[str, pd.Series] | None:
        if not self.fred:
            return None
        series_ids = {
            "2y": "DGS2",
            "10y": "DGS10",
            "30y": "DGS30",
            "3m": "DGS3MO",
        }
        yields = {}
        start = (datetime.now() - timedelta(days=365 * 2)).strftime("%Y-%m-%d")
        for label, sid in series_ids.items():
            try:
                yields[label] = self.fred.get_series(sid, observation_start=start)
            except Exception:
                yields[label] = pd.Series(dtype=float)
        return yields

    def get_vix(self) -> pd.DataFrame:
        vix = yf.Ticker("^VIX")
        return vix.history(period="1y", auto_adjust=True)

    def get_market_index(self, ticker: str = "SPY", period: str = "2y") -> pd.DataFrame:
        return self.get_stock_history(ticker, period)
