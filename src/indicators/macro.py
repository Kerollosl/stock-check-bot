import pandas as pd
from ..utils.data_fetcher import DataFetcher
from ..utils.errors import sanitize_error


class MacroAnalyzer:
    """Analyze macro conditions: Fed rate, yield curve, VIX, market trend. Scores in [0, 1]."""

    def __init__(self, fetcher: DataFetcher):
        self.fetcher = fetcher
        self._fed_rate = None
        self._yields = None
        self._vix = None
        self._spy = None
        self._loaded = False
        self._health = {}

    @staticmethod
    def _as_of(value) -> str | None:
        """Return the newest timestamp available in a fetched object."""
        if isinstance(value, dict):
            timestamps = [MacroAnalyzer._as_of(item) for item in value.values()]
            timestamps = [timestamp for timestamp in timestamps if timestamp]
            return max(timestamps) if timestamps else None

        if value is None or getattr(value, "empty", True):
            return None

        if isinstance(value, pd.Series):
            value = value.dropna()
            if value.empty:
                return None
        timestamp = value.index[-1]
        return timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)

    def _record_health(
        self,
        source: str,
        value,
        error: BaseException | None = None,
        required_keys: tuple[str, ...] = (),
        minimum_observations: int = 1,
    ):
        if error is not None:
            self._health[source] = {
                "status": "error",
                "as_of": None,
                "message": sanitize_error(error),
            }
            return

        if required_keys:
            series_health = {}
            missing_keys = []
            for key in required_keys:
                series = value.get(key) if isinstance(value, dict) else None
                observations = len(series.dropna()) if series is not None else 0
                series_health[key] = {
                    "as_of": self._as_of(series),
                    "observations": observations,
                }
                if observations < minimum_observations:
                    missing_keys.append(key)

            required_dates = [
                details["as_of"]
                for details in series_health.values()
                if details["as_of"]
            ]
            as_of = min(required_dates) if required_dates else None
        else:
            series_health = None
            missing_keys = []
            cleaned = value.dropna() if value is not None else None
            observations = len(cleaned) if cleaned is not None else 0
            as_of = self._as_of(value)
            if observations < minimum_observations:
                missing_keys.append(source)

        if missing_keys:
            self._health[source] = {
                "status": "partial",
                "as_of": as_of,
                "message": (
                    "Insufficient required data: " + ", ".join(missing_keys)
                ),
                **({"series": series_health} if series_health else {}),
            }
            return

        if as_of is None:
            self._health[source] = {
                "status": "unavailable",
                "as_of": None,
                "message": "No data returned",
            }
        else:
            self._health[source] = {
                "status": "ok",
                "as_of": as_of,
                **({"series": series_health} if series_health else {}),
            }

    def _load(self):
        if self._loaded:
            return

        try:
            self._fed_rate = self.fetcher.get_fed_funds_rate()
            self._record_health(
                "fred_funds",
                self._fed_rate,
                minimum_observations=3,
            )
        except Exception as error:
            self._fed_rate = pd.Series(dtype=float)
            self._record_health("fred_funds", self._fed_rate, error)

        try:
            self._yields = self.fetcher.get_treasury_yields()
            self._record_health(
                "treasury_yields",
                self._yields,
                required_keys=("2y", "10y"),
                minimum_observations=1,
            )
        except Exception as error:
            self._yields = {}
            self._record_health("treasury_yields", self._yields, error)

        try:
            self._vix = self.fetcher.get_vix()
            self._record_health("vix", self._vix)
        except Exception as error:
            self._vix = pd.DataFrame()
            self._record_health("vix", self._vix, error)

        try:
            self._spy = self.fetcher.get_market_index("SPY", "2y")
            self._record_health("spy", self._spy, minimum_observations=200)
        except Exception as error:
            self._spy = pd.DataFrame()
            self._record_health("spy", self._spy, error)

        self._loaded = True

    def get_data_health(self) -> dict[str, dict]:
        """Describe source availability and freshness for unattended reports."""
        self._load()
        return {source: details.copy() for source, details in self._health.items()}

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
