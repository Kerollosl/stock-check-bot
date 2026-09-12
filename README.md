# Stock Check Bot

A background research assistant for finding long-term quality businesses at a
large, evidence-supported discount. It discovers stocks beyond your watchlist,
checks financial quality, estimates conservative cash-flow scenarios, and sends
readable opportunity briefs. Screen results are research leads, not verified
bargains or trade instructions.

## Features

- **Weighted Composite Scoring** — Combines 14 signals across technical, fundamental, and macro categories into a single 0–1 score with BUY/SELL/HOLD signals
- **Technical Analysis** — MACD, Golden/Death Cross (SMA 50/200), RSI, Bollinger Bands, OBV volume trends
- **Fundamental Analysis** — Earnings surprises, P/E ratios, revenue & earnings growth
- **Macro Analysis** — Fed Funds Rate trends, yield curve (2y/10y spread), VIX fear gauge, SPY market trend, sector rotation momentum
- **Dip Detection** — Alerts on daily drops, weekly drops, and distance from 52-week highs
- **Strategy Backtesting** — Compare 5 built-in strategies (Momentum, Value, Macro-Driven, Balanced, Contrarian) against historical data
- **Rich Terminal Dashboard** — Color-coded market report with stock cards, macro overview, and backtest comparison tables
- **Dynamic discovery** — Screens the largest 750 eligible US-listed USD equities,
  rather than only the six personal-watchlist names; refreshes the universe each run
- **Quality and valuation gates** — Three years of positive owner cash flow,
  current quarterly statements, positive profit, resilient revenue, manageable
  leverage, a large drawdown, and a margin below the base valuation scenario
- **Opportunity briefs** — Designed HTML email with a plain-text alternative:
  business case, stress/base/upside values, research price, risks, and source links
- **Quiet monitoring** — Hourly checks during the US trading day, new-candidate
  alerts, thesis-change warnings, and a Friday roundup; unchanged ideas stay quiet

The older technical/macroeconomic dashboard and backtester remain available as
separate tools. Their blended BUY/SELL score no longer controls scheduled alerts.

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

# Structured report for automation
python -m src.main report \
  --output reports/latest_report.json \
  --summary reports/summary.md \
  --events reports/events.json

# Preview the new broad opportunity brief without sending email
python -m src.opportunity_monitor --mode weekly

# Inspect a particular company without sending
python -m src.opportunity_monitor --ticker ADBE --mode weekly

# Deliver only a qualifying alert (requires mail settings below)
python -m src.opportunity_monitor --send

# Override watchlist
python -m src.main scan -t AAPL -t MSFT -t NVDA
```

## Background monitoring and email

Open [Actions → Stock check](https://github.com/Kerollosl/stock-check-bot/actions/workflows/stock-check.yml).
Each run saves `brief.html`, a plain-text brief, a readable summary, the full
research report, and delivery status as a 30-day artifact. `Run workflow` defaults
to preview only; select `send` to deliver a qualifying alert or weekly brief.

The hourly schedule is `23 14-21 * * 1-5` in UTC. A New York exchange-calendar
check limits actual scans to trading sessions and the first 55 minutes after
the close, including holidays and early closes. The Friday roundup is at
21:43 UTC (5:43 PM Eastern daylight time / 4:43 PM Eastern standard time).
These are scheduled checks, not streaming or guaranteed real-time alerts.
[GitHub can delay or drop scheduled runs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

### Connect the designed emails

Add these [repository secrets](https://github.com/Kerollosl/stock-check-bot/settings/secrets/actions):

| Secret | Value |
| --- | --- |
| `SMTP_USER` | Your sending Gmail address |
| `SMTP_PASSWORD` | A Google app password, **not** your normal account password |
| `EMAIL_TO` | The single address that should receive the briefs |
| `SMTP_FROM` | Optional; defaults to `SMTP_USER` |

Create an [app password in your Google account](https://myaccount.google.com/apppasswords).
[Google requires 2-Step Verification and some accounts do not offer app passwords](https://support.google.com/accounts/answer/185833).
Do not commit credentials or paste them into an issue. A different SMTP provider
can be used with repository variables `SMTP_HOST` and `SMTP_PORT`; defaults are
`smtp.gmail.com` and `465`. Port 465 uses TLS directly; other ports require STARTTLS.

Until an SMTP password is connected, the same opportunity brief is delivered as
an improved GitHub issue. GitHub controls that notification's surrounding email
layout. Once SMTP is configured, opportunity messages go directly by email and
no duplicate stock-opportunity issue is opened. Authentication failures are
reported rather than silently falling back to GitHub.

### What triggers an email

- A newly qualifying research candidate, with at most three ideas per brief.
- An already surfaced candidate becomes at least another 8% cheaper, after a
  48-hour cooldown, or qualifies again after failing a business check.
- A previously surfaced company fails a business-quality check; missing data
  alone is not called a broken thesis.
- The Friday roundup, even when no stocks qualify. It explains the closest
  assessed names, exclusions, incomplete coverage, and the week's discoveries.

The sent-alert ledger advances only after the mail server accepts delivery or
GitHub confirms its fallback issue. Failed messages are retained for retry on a
later run, including weekly messages outside market hours. Email acceptance is
not proof of inbox placement; check Spam when first connecting the sender.

## How an opportunity is assessed

1. **Discover:** refresh the largest 750 eligible NYSE/Nasdaq listings above
   $5 billion in market value and 300,000 average daily shares traded. This is
   a capped US-listed universe, not every stock worldwide.
2. **Triage:** flag prices at least 15% below their 52-week highs, plus the
   personal watchlist and earlier alerted names. Rotate up to 60 detailed checks
   per run, reserving capacity for new discoveries. Earlier alerts remain
   tracked even if they leave the discovery universe.
3. **Check quality:** require positive net profit, at least a 10% operating
   margin, nonnegative latest revenue growth, net debt/EBITDA at most 3, and
   positive owner cash in three consecutive years and the latest four quarters.
   Owner cash is operating cash flow less absolute capital expenditure and
   stock-based compensation. It is a conservative proxy, not distributable cash.
4. **Normalize:** use the lower of trailing four-quarter owner cash and the
   median of three annual results; do not extrapolate a single unusually good year.
5. **Value:** calculate five-year cash-flow sensitivity scenarios. Base growth
   is capped at 0–8% using latest revenue growth as a screening assumption, with
   a 10% discount rate and 2% terminal growth. The stress case immediately cuts
   cash flow by 20%, uses no growth and a 12% discount rate. Current shares are
   held constant; net debt is checked separately, not subtracted a second time.
6. **Qualify for research:** require at least a 20% drawdown, 25% below the base
   scenario and no more than 35% modeled stress downside, with all quality/data
   checks passing. The displayed research price clears all three price rules
   if the business inputs hold. The stress case is not a worst-case floor.

Thresholds are explicit screening assumptions in `config.yaml`, not a
historically validated strategy. No claim of market outperformance is made.
Banking/financial services, real estate, non-USD financial statements and
unreconciled share/currency units are excluded from this cash-flow model.
Competitive advantage, management, accounting and the cause of a decline still
require reading company disclosures. Linked news is research context, not proof
of what caused the price move.

Financial snapshots refresh within 24 hours and sooner after a large daily move
or a passed earnings timestamp. Fiscal reports older than 150 days, stale
quotes, missing stock compensation and incomplete periods cannot qualify.
Intraday prices can be delayed; quote timestamps are shown. Data comes from
Yahoo Finance through yfinance, without a premium data-feed agreement.

### Operational limits and recovery

GitHub disables scheduled workflows in public repositories after 60 days of
inactivity. Re-enable the schedule if GitHub warns you; the bot does not create
fake commits to avoid that limit. To pause monitoring, disable `Stock check`
in Actions. Changing `watchlist.txt` changes the personal overlay; changing
`opportunity` settings changes broad discovery.

The queue and delivery ledger live in the GitHub Actions cache. Cache eviction
can reset remembered alerts, so rare repeats are possible. A corrupted ledger
withholds alerts and opens a service issue instead of guessing which were sent.
To recover, inspect and remove only caches prefixed `opportunity-state-v2-`, then
run a preview to rebuild the baseline. Keep the local `state/` directory when
running outside GitHub. Source snapshots and reports contain public market data;
mail credentials stay in secrets and are excluded from reports.

The legacy macro commands still use `FRED_API_KEY`. The new opportunity scanner
does not need FRED, an OpenAI API key, or a brokerage connection. It places no trades.

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
