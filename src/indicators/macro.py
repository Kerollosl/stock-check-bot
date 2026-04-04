import pandas as pd
import numpy as np
from ..utils.data_fetcher import DataFetcher


class MacroAnalyzer:
    """Analyze macro conditions: Fed rate, yield curve, VIX, market trend. Scores in [0, 1]."""

    def __init__(self, fetcher: DataFetcher):
        self.fetcher = fetcher
        self._fed_rate = None
        self._yields = None
        self._vix = None
        self._spy = None

    def _load(self):
        if self._fed_rate is None:
            self._fed_rate = self.fetcher.get_fed_funds_rate()
        if self._yields is None:
            self._yields = self.fetcher.get_treasury_yields()
        if self._vix is None:
            self._vix = self.fetcher.get_vix()
        if self._spy is None:
            self._spy = self.fetcher.get_market_index("SPY", "2y")

    def fed_funds_rate_score(self) -> float:
        """Score based on Fed Funds Rate trend. Falling rates = bullish."""
        self._load()
        if self._fed_rate is None or self._fed_rate.empty:
            return 0.5

        rate = self._fed_rate.dropna()
        if len(rate) < 3:
            return 0.5

        current = rate.iloc[-1]
        prev_3m = rate.iloc[-3] if len(rate) >= 3 else rate.iloc[0]

        change = current - prev_3m

        if change < -0.5:
            return 0.85  # Aggressive cutting — very bullish
        elif change < -0.25:
            return 0.75  # Cutting — bullish
        elif change < 0:
            return 0.6   # Slight cut
        elif change == 0:
            # Paused: level matters
            if current > 5:
                return 0.35  # High and paused
            elif current > 3:
                return 0.45
            else:
                return 0.55
        elif change < 0.25:
            return 0.4   # Slight hike
        elif change < 0.5:
            return 0.25  # Hiking — bearish
        else:
            return 0.15  # Aggressive hiking — very bearish

    def yield_curve_score(self) -> float:
        """Score based on 10y-2y spread. Inverted = bearish."""
        self._load()
        if self._yields is None:
            return 0.5

        y2 = self._yields.get("2y")
        y10 = self._yields.get("10y")

        if y2 is None or y10 is None or y2.empty or y10.empty:
            return 0.5

        y2_val = y2.dropna().iloc[-1]
        y10_val = y10.dropna().iloc[-1]
        spread = y10_val - y2_val

        if spread < -0.5:
            return 0.1   # Deeply inverted — strong recession signal
        elif spread < 0:
            return 0.25  # Inverted
        elif spread < 0.5:
            return 0.5   # Flat — neutral
        elif spread < 1.0:
            return 0.65  # Normal
        else:
            return 0.8   # Steep — growth expected

    def market_trend_score(self) -> float:
        """Score based on SPY trend vs its 50 and 200 SMA."""
        self._load()
        if self._spy is None or self._spy.empty or len(self._spy) < 200:
            return 0.5

        close = self._spy["Close"]
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()

        current = close.iloc[-1]
        s50 = sma50.iloc[-1]
        s200 = sma200.iloc[-1]

        if pd.isna(s50) or pd.isna(s200):
            return 0.5

        above_50 = current > s50
        above_200 = current > s200
        golden = s50 > s200

        if above_50 and above_200 and golden:
            return 0.85
        elif above_200 and golden:
            return 0.7
        elif above_200:
            return 0.55
        elif not above_200 and not golden:
            return 0.15
        else:
            return 0.35

    def vix_score(self) -> float:
        """Score based on VIX level. High VIX = fear = contrarian bullish (with nuance)."""
        self._load()
        if self._vix is None or self._vix.empty:
            return 0.5

        current_vix = self._vix["Close"].iloc[-1]
        avg_vix = self._vix["Close"].mean()

        if current_vix < 12:
            return 0.4   # Complacency — slightly bearish
        elif current_vix < 18:
            return 0.6   # Normal — slightly bullish
        elif current_vix < 25:
            return 0.5   # Elevated — neutral
        elif current_vix < 35:
            return 0.55  # Fear — contrarian buy signal emerging
        else:
            return 0.3   # Panic — too volatile, risk high

    def sector_rotation_score(self) -> float:
        """Simplified sector rotation: compare growth vs defensive sectors."""
        self._load()
        if self._spy is None or self._spy.empty:
            return 0.5

        # Use SPY 20-day momentum as proxy
        close = self._spy["Close"]
        if len(close) < 20:
            return 0.5

        momentum = (close.iloc[-1] / close.iloc[-20] - 1) * 100

        if momentum > 5:
            return 0.8   # Strong risk-on
        elif momentum > 2:
            return 0.65
        elif momentum > 0:
            return 0.55
        elif momentum > -2:
            return 0.45
        elif momentum > -5:
            return 0.35
        else:
            return 0.2   # Strong risk-off

    def get_all_scores(self) -> dict[str, float]:
        return {
            "fed_funds_rate": self.fed_funds_rate_score(),
            "yield_curve": self.yield_curve_score(),
            "market_trend": self.market_trend_score(),
            "vix": self.vix_score(),
            "sector_rotation": self.sector_rotation_score(),
        }
