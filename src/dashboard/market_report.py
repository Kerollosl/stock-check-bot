from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.columns import Columns
from rich import box

from ..utils.data_fetcher import DataFetcher
from ..indicators.technical import TechnicalAnalyzer
from ..indicators.fundamental import FundamentalAnalyzer
from ..indicators.macro import MacroAnalyzer
from ..scoring.weighted_scorer import WeightedScorer


class MarketDashboard:
    """Rich terminal dashboard showing market report scores."""

    def __init__(self, config_path: str = "config.yaml"):
        self.console = Console()
        self.fetcher = DataFetcher()
        self.scorer = WeightedScorer(config_path)

    def _score_color(self, score: float) -> str:
        if score >= 0.7:
            return "bold green"
        elif score >= 0.55:
            return "green"
        elif score >= 0.45:
            return "yellow"
        elif score >= 0.3:
            return "red"
        else:
            return "bold red"

    def _signal_color(self, signal: str) -> str:
        colors = {
            "STRONG BUY": "bold green",
            "BUY": "green",
            "HOLD": "yellow",
            "SELL": "red",
            "STRONG SELL": "bold red",
        }
        return colors.get(signal, "white")

    def _bar(self, score: float, width: int = 20) -> str:
        filled = int(score * width)
        return "█" * filled + "░" * (width - filled)

    def render_stock_card(self, ticker: str) -> Panel:
        """Render analysis for a single stock."""
        try:
            df = self.fetcher.get_stock_history(ticker, period="2y")
            if df.empty or len(df) < 50:
                return Panel(f"[red]Insufficient data for {ticker}[/red]", title=ticker)

            # Technical
            ta = TechnicalAnalyzer(df)
            tech_scores = ta.get_all_scores()
            dips = ta.detect_dips()

            # Fundamental
            earnings_data = self.fetcher.get_earnings(ticker)
            fa = FundamentalAnalyzer(earnings_data)
            fund_scores = fa.get_all_scores()

            # Macro (shared)
            macro = MacroAnalyzer(self.fetcher)
            macro_scores = macro.get_all_scores()

            # Composite
            result = self.scorer.compute_composite(tech_scores, fund_scores, macro_scores)
            composite = result["composite_score"]
            signal = result["signal"]
            confidence = result["confidence"]

            # Build card content
            lines = []

            # Price info
            price = df["Close"].iloc[-1]
            prev_close = df["Close"].iloc[-2]
            change_pct = (price / prev_close - 1) * 100
            change_color = "green" if change_pct >= 0 else "red"
            lines.append(
                f"Price: ${price:.2f}  [{change_color}]{change_pct:+.2f}%[/{change_color}]"
            )
            lines.append("")

            # Composite score
            sc = self._score_color(composite)
            sig_c = self._signal_color(signal)
            lines.append(f"[{sc}]Score: {composite:.2f}  {self._bar(composite)}[/{sc}]")
            lines.append(f"[{sig_c}]Signal: {signal}[/{sig_c}]  Confidence: {confidence}")
            lines.append("")

            # Technical breakdown
            lines.append("[bold]Technical[/bold]")
            for k, v in tech_scores.items():
                c = self._score_color(v)
                label = k.replace("_", " ").title()
                lines.append(f"  {label:.<22} [{c}]{v:.2f}[/{c}]")

            # Fundamental breakdown
            lines.append("[bold]Fundamental[/bold]")
            for k, v in fund_scores.items():
                c = self._score_color(v)
                label = k.replace("_", " ").title()
                lines.append(f"  {label:.<22} [{c}]{v:.2f}[/{c}]")

            # Dip alerts
            dip_alerts = []
            if dips["daily_dip"]:
                dip_alerts.append(f"Daily: {dips['daily_change_pct']}%")
            if dips["weekly_dip"]:
                dip_alerts.append(f"Weekly: {dips['weekly_change_pct']}%")
            if dips["major_dip_from_high"]:
                dip_alerts.append(f"From high: {dips['from_high_pct']}%")

            if dip_alerts:
                lines.append("")
                lines.append("[bold red]⚠ DIP ALERT[/bold red]")
                for alert in dip_alerts:
                    lines.append(f"  [red]{alert}[/red]")

            border_style = self._score_color(composite)
            return Panel(
                "\n".join(lines),
                title=f"[bold]{ticker}[/bold]",
                border_style=border_style,
                width=45,
            )
        except Exception as e:
            return Panel(f"[red]Error: {e}[/red]", title=ticker, width=45)

    def render_macro_panel(self) -> Panel:
        """Render macro environment overview."""
        macro = MacroAnalyzer(self.fetcher)
        scores = macro.get_all_scores()

        lines = []
        for k, v in scores.items():
            c = self._score_color(v)
            label = k.replace("_", " ").title()
            lines.append(f"  {label:.<25} [{c}]{v:.2f}  {self._bar(v, 15)}[/{c}]")

        avg_macro = sum(scores.values()) / len(scores) if scores else 0.5
        mc = self._score_color(avg_macro)
        lines.insert(0, f"[{mc}]Overall Macro Score: {avg_macro:.2f}[/{mc}]")
        lines.insert(1, "")

        return Panel(
            "\n".join(lines),
            title="[bold]Macro Environment[/bold]",
            border_style=mc,
        )

    def render_backtest_results(self, results: list[dict]) -> Panel:
        """Render backtest comparison table."""
        table = Table(
            title="Strategy Backtest Comparison",
            box=box.ROUNDED,
            show_lines=True,
        )
        table.add_column("Strategy", style="bold")
        table.add_column("Return %", justify="right")
        table.add_column("Annual %", justify="right")
        table.add_column("Sharpe", justify="right")
        table.add_column("Max DD %", justify="right")
        table.add_column("B&H %", justify="right")
        table.add_column("Alpha", justify="right")
        table.add_column("Trades", justify="right")

        for r in results:
            ret_c = "green" if r["total_return_pct"] > 0 else "red"
            alpha_c = "green" if r["alpha_vs_buyhold"] > 0 else "red"
            table.add_row(
                r["strategy"],
                f"[{ret_c}]{r['total_return_pct']:+.1f}%[/{ret_c}]",
                f"[{ret_c}]{r['annualized_return_pct']:+.1f}%[/{ret_c}]",
                f"{r['sharpe_ratio']:.3f}",
                f"[red]{r['max_drawdown_pct']:.1f}%[/red]",
                f"{r['buy_hold_return_pct']:+.1f}%",
                f"[{alpha_c}]{r['alpha_vs_buyhold']:+.1f}%[/{alpha_c}]",
                str(r["total_trades"]),
            )

        # Winner callout
        if results:
            winner = results[0]
            lines = [
                "",
                f"[bold green]🏆 Best Strategy: {winner['strategy']}[/bold green]",
                f"   Sharpe: {winner['sharpe_ratio']:.3f} | "
                f"Return: {winner['total_return_pct']:+.1f}% | "
                f"Alpha: {winner['alpha_vs_buyhold']:+.1f}%",
            ]
        else:
            lines = ["[yellow]No backtest results available[/yellow]"]

        from io import StringIO
        from rich.console import Console as _C

        buf = StringIO()
        _C(file=buf, force_terminal=True, width=100).print(table)
        content = buf.getvalue() + "\n".join(lines)

        return Panel(content, title="[bold]Backtest Results[/bold]")

    def render_full_dashboard(self, tickers: list[str], backtest_results: list[dict] | None = None):
        """Render the complete market dashboard."""
        self.console.clear()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.console.print(
            Panel(
                f"[bold]Stock Check Bot — Market Report[/bold]\n{now}",
                style="bold blue",
                expand=True,
            )
        )
        self.console.print()

        # Macro overview
        self.console.print(self.render_macro_panel())
        self.console.print()

        # Stock cards in rows of 3
        cards = []
        for ticker in tickers:
            self.console.print(f"[dim]Analyzing {ticker}...[/dim]", end="\r")
            cards.append(self.render_stock_card(ticker))

        for i in range(0, len(cards), 3):
            batch = cards[i : i + 3]
            self.console.print(Columns(batch, equal=True, expand=True))
            self.console.print()

        # Backtest results
        if backtest_results:
            self.console.print(self.render_backtest_results(backtest_results))
            self.console.print()

        self.console.print(
            "[dim]Scores: 0.0 (very bearish) → 1.0 (very bullish) | "
            "Signals remove emotion — trust the algorithm[/dim]"
        )
