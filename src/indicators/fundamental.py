import pandas as pd
import numpy as np


class FundamentalAnalyzer:
    """Analyze earnings, valuations, and growth metrics. Returns scores in [0, 1]."""

    def __init__(self, earnings_data: dict):
        self.data = earnings_data
        self.info = earnings_data.get("info", {})

    def earnings_surprise_score(self) -> float:
        """Score based on recent earnings surprises (beat/miss)."""
        hist = self.data.get("earnings_history")
        if hist is None or (isinstance(hist, pd.DataFrame) and hist.empty):
            return 0.5

        if isinstance(hist, pd.DataFrame) and "epsActual" in hist.columns and "epsEstimate" in hist.columns:
            recent = hist.tail(4)
            surprises = []
            for _, row in recent.iterrows():
                actual = row.get("epsActual")
                estimate = row.get("epsEstimate")
                if pd.notna(actual) and pd.notna(estimate) and estimate != 0:
                    surprises.append((actual - estimate) / abs(estimate))

            if not surprises:
                return 0.5

            avg_surprise = np.mean(surprises)
            consecutive_beats = sum(1 for s in surprises if s > 0)

            score = 0.5 + avg_surprise * 2  # Scale surprise
            score += (consecutive_beats / len(surprises) - 0.5) * 0.2

            return np.clip(score, 0.0, 1.0)

        return 0.5

    def pe_ratio_score(self) -> float:
        """Score based on P/E ratio relative to historical norms."""
        pe = self.data.get("pe_ratio")
        forward_pe = self.data.get("forward_pe")

        if pe is None and forward_pe is None:
            return 0.5

        score = 0.5
        if pe is not None:
            if pe < 0:
                score = 0.2  # Negative earnings
            elif pe < 12:
                score = 0.8  # Undervalued
            elif pe < 20:
                score = 0.65  # Fair value
            elif pe < 30:
                score = 0.45  # Slightly expensive
            elif pe < 50:
                score = 0.3  # Expensive
            else:
                score = 0.15  # Very expensive

        # Adjust if forward P/E shows improvement
        if forward_pe is not None and pe is not None and pe > 0 and forward_pe > 0:
            if forward_pe < pe:
                score = min(score + 0.1, 1.0)  # Earnings expected to grow
            elif forward_pe > pe * 1.2:
                score = max(score - 0.1, 0.0)  # Earnings expected to decline

        return score

    def revenue_growth_score(self) -> float:
        """Score based on revenue growth rate."""
        growth = self.data.get("revenue_growth")
        if growth is None:
            return 0.5

        if growth > 0.30:
            return 0.9
        elif growth > 0.15:
            return 0.75
        elif growth > 0.05:
            return 0.6
        elif growth > 0:
            return 0.5
        elif growth > -0.10:
            return 0.35
        else:
            return 0.15

    def earnings_growth_score(self) -> float:
        """Score based on earnings growth rate."""
        growth = self.data.get("earnings_growth")
        if growth is None:
            return 0.5

        if growth > 0.30:
            return 0.9
        elif growth > 0.15:
            return 0.75
        elif growth > 0.05:
            return 0.6
        elif growth > 0:
            return 0.5
        elif growth > -0.10:
            return 0.35
        else:
            return 0.15

    def get_all_scores(self) -> dict[str, float]:
        return {
            "earnings_surprise": self.earnings_surprise_score(),
            "pe_ratio": self.pe_ratio_score(),
            "revenue_growth": self.revenue_growth_score(),
            "earnings_growth": self.earnings_growth_score(),
        }
