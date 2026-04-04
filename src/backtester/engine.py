import pandas as pd
import numpy as np
from ..indicators.technical import TechnicalAnalyzer
from ..utils.data_fetcher import DataFetcher


class BacktestEngine:
    """Backtest a strategy configuration against historical data."""

    def __init__(
        self,
        initial_capital: float = 100000,
        commission_pct: float = 0.001,
        slippage_pct: float = 0.001,
    ):
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.fetcher = DataFetcher()

    def run(
        self,
        ticker: str,
        strategy_config: dict,
        start_date: str = "2020-01-01",
        end_date: str = "2025-12-31",
    ) -> dict:
        """Run backtest for a single ticker with a given strategy weight config."""
        df = self.fetcher.get_stock_history_range(ticker, start_date, end_date)
        if df.empty or len(df) < 200:
            return {"error": f"Insufficient data for {ticker}"}

        weights = strategy_config["weights"]
        thresholds = strategy_config["thresholds"]

        # Pre-compute all technical indicators
        analyzer = TechnicalAnalyzer(df)
        tech_df = analyzer.df

        # Simulate trading
        capital = self.initial_capital
        shares = 0
        position = "cash"
        trades = []
        portfolio_values = []

        # Use a rolling window approach
        for i in range(200, len(tech_df)):
            window = tech_df.iloc[: i + 1]
            current_price = window["Close"].iloc[-1]

            # Compute technical scores on the window
            ta = TechnicalAnalyzer(window.reset_index(drop=True)
                                   if not isinstance(window.index, pd.DatetimeIndex)
                                   else window)
            tech_scores = ta.get_all_scores()

            # Use neutral fundamental/macro for pure backtest (no historical fundamentals available)
            fund_scores = {
                "earnings_surprise": 0.5,
                "pe_ratio": 0.5,
                "revenue_growth": 0.5,
                "earnings_growth": 0.5,
            }
            macro_scores = {
                "fed_funds_rate": 0.5,
                "yield_curve": 0.5,
                "market_trend": 0.5,
                "vix": 0.5,
                "sector_rotation": 0.5,
            }

            composite = self._compute_weighted(
                tech_scores, fund_scores, macro_scores, weights
            )

            portfolio_val = capital + shares * current_price
            portfolio_values.append(
                {"date": tech_df.index[i], "value": portfolio_val, "score": composite}
            )

            # Trading logic
            if composite >= thresholds["buy"] and position == "cash":
                # BUY
                cost_per_share = current_price * (1 + self.slippage_pct)
                max_shares = int(capital / (cost_per_share * (1 + self.commission_pct)))
                if max_shares > 0:
                    total_cost = max_shares * cost_per_share * (1 + self.commission_pct)
                    capital -= total_cost
                    shares = max_shares
                    position = "long"
                    trades.append({
                        "date": tech_df.index[i],
                        "action": "BUY",
                        "price": current_price,
                        "shares": max_shares,
                        "score": composite,
                    })

            elif composite <= thresholds["sell"] and position == "long":
                # SELL
                proceeds_per_share = current_price * (1 - self.slippage_pct)
                proceeds = shares * proceeds_per_share * (1 - self.commission_pct)
                capital += proceeds
                trades.append({
                    "date": tech_df.index[i],
                    "action": "SELL",
                    "price": current_price,
                    "shares": shares,
                    "score": composite,
                })
                shares = 0
                position = "cash"

        # Final portfolio value
        final_price = tech_df["Close"].iloc[-1]
        final_value = capital + shares * final_price

        # Compute metrics
        pv = pd.DataFrame(portfolio_values)
        if pv.empty:
            return {"error": "No trading signals generated"}

        total_return = (final_value / self.initial_capital - 1) * 100
        days = (pv["date"].iloc[-1] - pv["date"].iloc[0]).days
        years = days / 365.25 if days > 0 else 1
        annualized = ((final_value / self.initial_capital) ** (1 / years) - 1) * 100

        # Max drawdown
        peak = pv["value"].expanding().max()
        drawdown = (pv["value"] - peak) / peak
        max_drawdown = drawdown.min() * 100

        # Sharpe ratio (approximate)
        daily_returns = pv["value"].pct_change().dropna()
        sharpe = 0.0
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)

        # Buy and hold comparison
        bh_start = tech_df["Close"].iloc[200]
        bh_return = (final_price / bh_start - 1) * 100

        return {
            "ticker": ticker,
            "strategy": strategy_config.get("name", "Custom"),
            "total_return_pct": round(total_return, 2),
            "annualized_return_pct": round(annualized, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe, 3),
            "total_trades": len(trades),
            "buy_hold_return_pct": round(bh_return, 2),
            "alpha_vs_buyhold": round(total_return - bh_return, 2),
            "final_value": round(final_value, 2),
            "initial_capital": self.initial_capital,
            "period": f"{start_date} to {end_date}",
            "trades": trades,
        }

    def _compute_weighted(
        self,
        tech: dict,
        fund: dict,
        macro: dict,
        weights: dict,
    ) -> float:
        total = 0.0
        w_sum = 0.0
        for cat_name, scores in [("technical", tech), ("fundamental", fund), ("macro", macro)]:
            cat_w = weights.get(cat_name, {})
            for k, v in scores.items():
                w = cat_w.get(k, 0.0)
                total += v * w
                w_sum += w
        return total / w_sum if w_sum > 0 else 0.5

    def compare_strategies(
        self,
        ticker: str,
        strategies: dict[str, dict],
        start_date: str = "2020-01-01",
        end_date: str = "2025-12-31",
    ) -> list[dict]:
        """Run multiple strategies and return sorted results."""
        results = []
        for name, config in strategies.items():
            config_with_name = {**config, "name": config.get("name", name)}
            result = self.run(ticker, config_with_name, start_date, end_date)
            if "error" not in result:
                results.append(result)

        results.sort(key=lambda r: r["sharpe_ratio"], reverse=True)
        return results
