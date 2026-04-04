import os
from datetime import datetime

import yaml
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse

from ..utils.data_fetcher import DataFetcher
from ..indicators.technical import TechnicalAnalyzer
from ..indicators.fundamental import FundamentalAnalyzer
from ..indicators.macro import MacroAnalyzer
from ..scoring.weighted_scorer import WeightedScorer
from ..backtester.engine import BacktestEngine
from ..backtester.strategies import STRATEGIES

app = FastAPI(title="Stock Check Bot")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

fetcher = DataFetcher()
CONFIG_PATH = os.environ.get("SCB_CONFIG", "config.yaml")

WATCHLIST_FILE = "watchlist.txt"
_macro_cache = {"scores": None, "ts": None}


def get_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_watchlist() -> list[str]:
    """Load tickers from watchlist.txt (one per line), falling back to config.yaml."""
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE) as f:
            tickers = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
        if tickers:
            return tickers
    cfg = get_config()
    return cfg.get("watchlist", [])


def get_macro_scores():
    now = datetime.now()
    if _macro_cache["scores"] and _macro_cache["ts"] and (now - _macro_cache["ts"]).seconds < 300:
        return _macro_cache["scores"]
    macro = MacroAnalyzer(fetcher)
    scores = macro.get_all_scores()
    _macro_cache["scores"] = scores
    _macro_cache["ts"] = now
    return scores


@app.get("/")
async def dashboard(request: Request):
    tickers = load_watchlist()
    return templates.TemplateResponse(
        request, "dashboard.html", {"tickers": tickers}
    )


@app.get("/api/config")
async def api_config():
    return {"watchlist": load_watchlist()}


@app.get("/api/macro")
async def api_macro():
    scores = get_macro_scores()
    avg = sum(scores.values()) / len(scores) if scores else 0.5
    return {"scores": scores, "overall": round(avg, 4)}


@app.get("/api/stock/{ticker}")
async def api_stock(ticker: str):
    try:
        df = fetcher.get_stock_history(ticker, period="2y")
        if df.empty or len(df) < 50:
            return JSONResponse({"error": f"Insufficient data for {ticker}"}, status_code=404)

        ta = TechnicalAnalyzer(df)
        tech_scores = ta.get_all_scores()
        raw_dips = ta.detect_dips()
        dips = {}
        for k, v in raw_dips.items():
            if hasattr(v, 'item'):
                dips[k] = v.item()
            else:
                dips[k] = v

        earnings = fetcher.get_earnings(ticker)
        fa = FundamentalAnalyzer(earnings)
        fund_scores = fa.get_all_scores()

        macro_scores = get_macro_scores()

        scorer = WeightedScorer(CONFIG_PATH)
        result = scorer.compute_composite(tech_scores, fund_scores, macro_scores)

        price = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-2])
        change_pct = round((price / prev - 1) * 100, 2)

        high_52w = float(df["Close"].tail(252).max()) if len(df) >= 252 else float(df["Close"].max())

        sparkline = [round(float(v), 2) for v in df["Close"].tail(60).tolist()]
        volume = [int(v) for v in df["Volume"].tail(60).tolist()]

        return {
            "ticker": ticker,
            "price": round(price, 2),
            "change_pct": change_pct,
            "high_52w": round(high_52w, 2),
            "composite_score": result["composite_score"],
            "signal": result["signal"],
            "confidence": result["confidence"],
            "technical": {k: round(v, 4) for k, v in tech_scores.items()},
            "fundamental": {k: round(v, 4) for k, v in fund_scores.items()},
            "macro": {k: round(v, 4) for k, v in macro_scores.items()},
            "dips": dips,
            "breakdown": result["breakdown"],
            "sparkline": sparkline,
            "volume": volume,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/history/{ticker}")
async def api_history(ticker: str, period: str = "1y"):
    df = fetcher.get_stock_history(ticker, period=period)
    if df.empty:
        return JSONResponse({"error": "No data"}, status_code=404)

    ohlc = []
    vol = []
    for idx, row in df.iterrows():
        ts = int(idx.timestamp() * 1000)
        ohlc.append({
            "x": ts,
            "y": [round(float(row["Open"]), 2), round(float(row["High"]), 2),
                  round(float(row["Low"]), 2), round(float(row["Close"]), 2)],
        })
        vol.append({"x": ts, "y": int(row["Volume"])})

    return {"ticker": ticker, "ohlc": ohlc, "volume": vol}


@app.get("/api/backtest/{ticker}")
async def api_backtest(ticker: str):
    cfg = get_config()
    bt_cfg = cfg.get("backtest", {})

    engine = BacktestEngine(
        initial_capital=bt_cfg.get("initial_capital", 100000),
        commission_pct=bt_cfg.get("commission_pct", 0.001),
        slippage_pct=bt_cfg.get("slippage_pct", 0.001),
    )

    results = engine.compare_strategies(
        ticker, STRATEGIES,
        bt_cfg.get("start_date", "2020-01-01"),
        bt_cfg.get("end_date", "2025-12-31"),
    )

    for r in results:
        r["trades"] = [
            {**t, "date": str(t["date"])} for t in r.get("trades", [])
        ]

    return {"ticker": ticker, "results": results}
