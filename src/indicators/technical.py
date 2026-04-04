import pandas as pd
import numpy as np
from ta.trend import MACD, SMAIndicator, EMAIndicator
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands
from ta.volume import OnBalanceVolumeIndicator


class TechnicalAnalyzer:
    """Compute technical indicators and return normalized signals in [0, 1]."""

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self._compute_all()

    def _compute_all(self):
        close = self.df["Close"]
        high = self.df["High"]
        low = self.df["Low"]
        volume = self.df["Volume"]

        # MACD
        macd = MACD(close)
        self.df["macd"] = macd.macd()
        self.df["macd_signal"] = macd.macd_signal()
        self.df["macd_hist"] = macd.macd_diff()

        # Moving averages for golden/death cross
        self.df["sma_50"] = SMAIndicator(close, window=50).sma_indicator()
        self.df["sma_200"] = SMAIndicator(close, window=200).sma_indicator()
        self.df["ema_12"] = EMAIndicator(close, window=12).ema_indicator()
        self.df["ema_26"] = EMAIndicator(close, window=26).ema_indicator()

        # RSI
        self.df["rsi"] = RSIIndicator(close, window=14).rsi()

        # Bollinger Bands
        bb = BollingerBands(close, window=20, window_dev=2)
        self.df["bb_upper"] = bb.bollinger_hband()
        self.df["bb_lower"] = bb.bollinger_lband()
        self.df["bb_mid"] = bb.bollinger_mavg()
        self.df["bb_pct"] = bb.bollinger_pband()

        # Stochastic
        stoch = StochasticOscillator(high, low, close)
        self.df["stoch_k"] = stoch.stoch()
        self.df["stoch_d"] = stoch.stoch_signal()

        # On-Balance Volume
        self.df["obv"] = OnBalanceVolumeIndicator(close, volume).on_balance_volume()

        # Volume moving average
        self.df["vol_sma_20"] = volume.rolling(window=20).mean()

    def macd_signal_score(self) -> float:
        """MACD histogram direction and crossover. Returns 0-1."""
        row = self.df.iloc[-1]
        prev = self.df.iloc[-2]

        hist = row["macd_hist"]
        prev_hist = prev["macd_hist"]

        if pd.isna(hist) or pd.isna(prev_hist):
            return 0.5

        # Bullish: histogram positive and increasing
        if hist > 0 and hist > prev_hist:
            return min(0.5 + abs(hist) / (abs(row["macd"]) + 1e-9) * 0.5, 1.0)
        # Bearish: histogram negative and decreasing
        elif hist < 0 and hist < prev_hist:
            return max(0.5 - abs(hist) / (abs(row["macd"]) + 1e-9) * 0.5, 0.0)
        # Crossover detection
        elif prev_hist < 0 < hist:
            return 0.8  # Bullish crossover
        elif prev_hist > 0 > hist:
            return 0.2  # Bearish crossover
        else:
            return 0.5

    def golden_death_cross_score(self) -> float:
        """SMA 50/200 cross. Returns 0-1."""
        row = self.df.iloc[-1]
        if pd.isna(row["sma_50"]) or pd.isna(row["sma_200"]):
            return 0.5

        ratio = row["sma_50"] / row["sma_200"]

        # Check for recent crossover (last 5 days)
        recent = self.df.tail(10)
        cross_above = any(
            (recent["sma_50"].iloc[i] > recent["sma_200"].iloc[i])
            and (recent["sma_50"].iloc[i - 1] <= recent["sma_200"].iloc[i - 1])
            for i in range(1, len(recent))
            if not pd.isna(recent["sma_50"].iloc[i])
            and not pd.isna(recent["sma_200"].iloc[i])
        )
        cross_below = any(
            (recent["sma_50"].iloc[i] < recent["sma_200"].iloc[i])
            and (recent["sma_50"].iloc[i - 1] >= recent["sma_200"].iloc[i - 1])
            for i in range(1, len(recent))
            if not pd.isna(recent["sma_50"].iloc[i])
            and not pd.isna(recent["sma_200"].iloc[i])
        )

        if cross_above:
            return 0.9  # Golden cross
        elif cross_below:
            return 0.1  # Death cross
        elif ratio > 1.0:
            return min(0.5 + (ratio - 1.0) * 5, 0.85)
        else:
            return max(0.5 - (1.0 - ratio) * 5, 0.15)

    def rsi_score(self) -> float:
        """RSI-based score. Oversold = bullish, overbought = bearish."""
        rsi = self.df["rsi"].iloc[-1]
        if pd.isna(rsi):
            return 0.5

        if rsi <= 30:
            return 0.85  # Oversold — bullish opportunity
        elif rsi >= 70:
            return 0.15  # Overbought — bearish warning
        else:
            # Linear scale: 30->0.85, 50->0.5, 70->0.15
            return 0.85 - (rsi - 30) / 40 * 0.7

    def bollinger_score(self) -> float:
        """Price position within Bollinger Bands."""
        row = self.df.iloc[-1]
        bb_pct = row["bb_pct"]
        if pd.isna(bb_pct):
            return 0.5

        # Near lower band = bullish, near upper band = bearish
        if bb_pct <= 0:
            return 0.85
        elif bb_pct >= 1:
            return 0.15
        else:
            return 0.85 - bb_pct * 0.7

    def volume_trend_score(self) -> float:
        """Volume relative to 20-day average and OBV trend."""
        row = self.df.iloc[-1]
        vol = row["Volume"]
        vol_avg = row["vol_sma_20"]

        if pd.isna(vol) or pd.isna(vol_avg) or vol_avg == 0:
            return 0.5

        vol_ratio = vol / vol_avg

        # OBV trend (last 5 days)
        obv_recent = self.df["obv"].tail(5)
        obv_slope = 0.0
        if len(obv_recent.dropna()) >= 2:
            x = np.arange(len(obv_recent))
            obv_vals = obv_recent.values
            mask = ~np.isnan(obv_vals)
            if mask.sum() >= 2:
                slope = np.polyfit(x[mask], obv_vals[mask], 1)[0]
                obv_slope = np.sign(slope)

        # High volume + price up + OBV up = bullish
        price_change = self.df["Close"].pct_change().iloc[-1]

        if vol_ratio > 1.5 and price_change > 0 and obv_slope > 0:
            return 0.8
        elif vol_ratio > 1.5 and price_change < 0 and obv_slope < 0:
            return 0.2
        elif obv_slope > 0:
            return 0.6
        elif obv_slope < 0:
            return 0.4
        else:
            return 0.5

    def get_all_scores(self) -> dict[str, float]:
        return {
            "macd": self.macd_signal_score(),
            "golden_death_cross": self.golden_death_cross_score(),
            "rsi": self.rsi_score(),
            "bollinger": self.bollinger_score(),
            "volume_trend": self.volume_trend_score(),
        }

    def detect_dips(
        self, daily_threshold: float = -3.0, weekly_threshold: float = -7.0,
        from_high_threshold: float = -15.0, lookback: int = 252,
    ) -> dict:
        close = self.df["Close"]
        daily_change = close.pct_change().iloc[-1] * 100
        weekly_change = (close.iloc[-1] / close.iloc[-5] - 1) * 100 if len(close) >= 5 else 0
        high_lookback = close.tail(lookback).max()
        from_high = (close.iloc[-1] / high_lookback - 1) * 100

        return {
            "daily_change_pct": round(daily_change, 2),
            "weekly_change_pct": round(weekly_change, 2),
            "from_high_pct": round(from_high, 2),
            "daily_dip": daily_change <= daily_threshold,
            "weekly_dip": weekly_change <= weekly_threshold,
            "major_dip_from_high": from_high <= from_high_threshold,
        }
