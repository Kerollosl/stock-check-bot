# Stock Check Bot

Automated, emotion-free stock analysis using weighted algorithmic scoring across technical indicators, fundamentals, and macro conditions.

## Features

- **Weighted Composite Scoring** — Combines 14 signals across technical, fundamental, and macro categories into a single 0–1 score with BUY/SELL/HOLD signals
- **Technical Analysis** — MACD, Golden/Death Cross (SMA 50/200), RSI, Bollinger Bands, OBV volume trends
- **Fundamental Analysis** — Earnings surprises, P/E ratios, revenue & earnings growth
- **Macro Analysis** — Fed Funds Rate trends, yield curve (2y/10y spread), VIX fear gauge, SPY market trend, sector rotation momentum
- **Dip Detection** — Alerts on daily drops, weekly drops, and distance from 52-week highs
- **Strategy Backtesting** — Compare 5 built-in strategies (Momentum, Value, Macro-Driven, Balanced, Contrarian) against historical data
- **Rich Terminal Dashboard** — Color-coded market report with stock cards, macro overview, and backtest comparison tables

## Setup

```bash
# Clone and install
git clone https://github.com/YOUR_USERNAME/stock-check-bot.git
cd stock-check-bot
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your FRED API key (free at https://fred.stlouisfed.org/docs/api/api_key.html)
```

## Usage

```bash
# Full dashboard for your watchlist
python -m src.main scan

# Quick single-ticker check
python -m src.main check AAPL

# Backtest all strategies on a ticker
python -m src.main backtest -t SPY

# Full dashboard + backtest
python -m src.main full

# Override watchlist
python -m src.main scan -t AAPL -t MSFT -t NVDA
```

## Configuration

Edit `config.yaml` to customize:
- **Watchlist** — Tickers to monitor
- **Weights** — How much each signal contributes to the composite score
- **Thresholds** — Score levels that trigger BUY/SELL/HOLD
- **Dip Detection** — Percentage drops that trigger alerts
- **Backtest** — Date range, starting capital, commission/slippage

## Built-in Strategies

| Strategy | Focus |
|----------|-------|
| Momentum Hunter | Trend-following (MACD, crosses, market trend) |
| Value Investor | Fundamentals (earnings, P/E, growth) |
| Macro Driven | Rates, yield curve, VIX |
| Balanced Blend | Equal weight across all categories |
| Contrarian Dip Buyer | RSI, Bollinger, VIX — buys fear, sells greed |

## Architecture

```
src/
├── main.py              # CLI entry point (click)
├── indicators/
│   ├── technical.py     # MACD, RSI, Bollinger, crosses, volume
│   ├── fundamental.py   # Earnings, P/E, growth metrics
│   └── macro.py         # Fed rate, yields, VIX, market trend
├── scoring/
│   └── weighted_scorer.py  # Combines all signals with configurable weights
├── backtester/
│   ├── engine.py        # Historical simulation engine
│   └── strategies.py    # 5 pre-built strategy configurations
├── dashboard/
│   └── market_report.py # Rich terminal dashboard
└── utils/
    └── data_fetcher.py  # yfinance + FRED API data layer
```

## Data Sources

- **Stock data**: [yfinance](https://github.com/ranaroussi/yfinance) (free, no API key)
- **Macro data**: [FRED API](https://fred.stlouisfed.org/) (free API key required)
- **VIX**: Yahoo Finance via yfinance
