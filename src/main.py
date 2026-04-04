import os
import sys

import click
import yaml
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
console = Console()


@click.group()
def cli():
    """Stock Check Bot — Weighted algorithmic analysis to remove emotion from investing."""
    pass


@cli.command()
@click.option("--config", default="config.yaml", help="Path to config file")
@click.option("--ticker", "-t", multiple=True, help="Override watchlist with specific tickers")
def scan(config, ticker):
    """Run full analysis on your watchlist and display the dashboard."""
    from .utils.data_fetcher import DataFetcher
    from .indicators.macro import MacroAnalyzer
    from .dashboard.market_report import MarketDashboard

    with open(config) as f:
        cfg = yaml.safe_load(f)

    tickers = list(ticker) if ticker else cfg.get("watchlist", [])
    if not tickers:
        console.print("[red]No tickers specified. Set watchlist in config.yaml or use -t TICKER[/red]")
        return

    dashboard = MarketDashboard(config)
    dashboard.render_full_dashboard(tickers)


@cli.command()
@click.option("--config", default="config.yaml", help="Path to config file")
@click.option("--ticker", "-t", default="SPY", help="Ticker to backtest")
@click.option("--start", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--end", default=None, help="End date (YYYY-MM-DD)")
def backtest(config, ticker, start, end):
    """Backtest all strategies against a ticker and find the best one."""
    from .backtester.engine import BacktestEngine
    from .backtester.strategies import STRATEGIES
    from .dashboard.market_report import MarketDashboard

    with open(config) as f:
        cfg = yaml.safe_load(f)

    bt_cfg = cfg.get("backtest", {})
    start_date = start or bt_cfg.get("start_date", "2020-01-01")
    end_date = end or bt_cfg.get("end_date", "2025-12-31")
    initial_capital = bt_cfg.get("initial_capital", 100000)

    console.print(f"[bold]Backtesting {ticker} from {start_date} to {end_date}[/bold]")
    console.print(f"Initial capital: ${initial_capital:,.0f}\n")

    engine = BacktestEngine(
        initial_capital=initial_capital,
        commission_pct=bt_cfg.get("commission_pct", 0.001),
        slippage_pct=bt_cfg.get("slippage_pct", 0.001),
    )

    console.print("[dim]Running strategies...[/dim]")
    results = engine.compare_strategies(ticker, STRATEGIES, start_date, end_date)

    dashboard = MarketDashboard(config)
    console.print()
    console.print(dashboard.render_backtest_results(results))

    if results:
        console.print(f"\n[bold green]Recommendation: Use '{results[0]['strategy']}' strategy[/bold green]")
        console.print("[dim]Based on highest risk-adjusted returns (Sharpe ratio)[/dim]")


@cli.command()
@click.option("--config", default="config.yaml", help="Path to config file")
@click.option("--ticker", "-t", multiple=True, help="Override watchlist with specific tickers")
def full(config, ticker):
    """Run full analysis + backtest and display everything."""
    from .utils.data_fetcher import DataFetcher
    from .backtester.engine import BacktestEngine
    from .backtester.strategies import STRATEGIES
    from .dashboard.market_report import MarketDashboard

    with open(config) as f:
        cfg = yaml.safe_load(f)

    tickers = list(ticker) if ticker else cfg.get("watchlist", [])
    bt_cfg = cfg.get("backtest", {})

    # Backtest first ticker
    bt_ticker = tickers[0] if tickers else "SPY"
    engine = BacktestEngine(
        initial_capital=bt_cfg.get("initial_capital", 100000),
        commission_pct=bt_cfg.get("commission_pct", 0.001),
        slippage_pct=bt_cfg.get("slippage_pct", 0.001),
    )

    console.print(f"[dim]Backtesting strategies on {bt_ticker}...[/dim]")
    results = engine.compare_strategies(
        bt_ticker,
        STRATEGIES,
        bt_cfg.get("start_date", "2020-01-01"),
        bt_cfg.get("end_date", "2025-12-31"),
    )

    dashboard = MarketDashboard(config)
    dashboard.render_full_dashboard(tickers, backtest_results=results)


@cli.command()
@click.argument("ticker")
@click.option("--config", default="config.yaml", help="Path to config file")
def check(ticker, config):
    """Quick check on a single ticker — scores + dip detection."""
    from .utils.data_fetcher import DataFetcher
    from .indicators.technical import TechnicalAnalyzer
    from .indicators.fundamental import FundamentalAnalyzer
    from .indicators.macro import MacroAnalyzer
    from .scoring.weighted_scorer import WeightedScorer

    fetcher = DataFetcher()
    scorer = WeightedScorer(config)

    console.print(f"[bold]Checking {ticker}...[/bold]\n")

    df = fetcher.get_stock_history(ticker)
    if df.empty:
        console.print(f"[red]No data for {ticker}[/red]")
        return

    ta = TechnicalAnalyzer(df)
    tech_scores = ta.get_all_scores()
    dips = ta.detect_dips()

    earnings = fetcher.get_earnings(ticker)
    fa = FundamentalAnalyzer(earnings)
    fund_scores = fa.get_all_scores()

    macro = MacroAnalyzer(fetcher)
    macro_scores = macro.get_all_scores()

    result = scorer.compute_composite(tech_scores, fund_scores, macro_scores)

    # Output
    price = df["Close"].iloc[-1]
    console.print(f"Price: ${price:.2f}")
    console.print(f"Composite Score: {result['composite_score']:.4f}")
    console.print(f"Signal: [bold]{result['signal']}[/bold]")
    console.print(f"Confidence: {result['confidence']}\n")

    console.print("[bold]Breakdown:[/bold]")
    for key, info in result["breakdown"].items():
        console.print(f"  {key:.<35} score={info['score']:.3f}  w={info['weight']:.3f}  contrib={info['contribution']:.4f}")

    console.print(f"\n[bold]Dip Check:[/bold]")
    console.print(f"  Daily:  {dips['daily_change_pct']:+.2f}%  {'[red]DIP[/red]' if dips['daily_dip'] else '[green]OK[/green]'}")
    console.print(f"  Weekly: {dips['weekly_change_pct']:+.2f}%  {'[red]DIP[/red]' if dips['weekly_dip'] else '[green]OK[/green]'}")
    console.print(f"  From High: {dips['from_high_pct']:+.2f}%  {'[red]MAJOR DIP[/red]' if dips['major_dip_from_high'] else '[green]OK[/green]'}")


if __name__ == "__main__":
    cli()
