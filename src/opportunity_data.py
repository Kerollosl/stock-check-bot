"""Live Yahoo discovery and auditable inputs for the long-term stock scanner.

This module fetches evidence, not recommendations. A successful capped screen
means the configured largest-company scope was fetched, not the entire market.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import yfinance as yf
from curl_cffi import requests

from src.utils.errors import sanitize_error


class _BoundedSession(requests.Session):
    """Cap individual requests, including yfinance methods with no timeout arg."""

    def request(self, method, url, *args, **kwargs):
        timeout = kwargs.get("timeout")
        kwargs["timeout"] = min(timeout, 12) if isinstance(timeout, (int, float)) else 12
        return super().request(method, url, *args, **kwargs)


_SESSION = _BoundedSession(impersonate="chrome")
_EXCHANGES = {"NYQ", "NMS", "NGM"}
_FALLBACK_SCREENS = (
    "most_actives", "day_losers", "undervalued_large_caps", "growth_technology_stocks"
)
_QUOTE_FIELDS = (
    "symbol", "quoteType", "currency", "exchange", "fullExchangeName", "shortName",
    "longName", "regularMarketPrice", "regularMarketTime", "regularMarketChange",
    "regularMarketChangePercent", "regularMarketPreviousClose", "marketCap",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "averageDailyVolume3Month",
    "averageDailyVolume10Day", "regularMarketVolume", "earningsTimestamp",
    "earningsTimestampStart", "earningsTimestampEnd", "sharesOutstanding",
)
_MARKET_FIELDS = (
    "regularMarketPrice", "regularMarketTime", "regularMarketChange",
    "regularMarketChangePercent", "regularMarketPreviousClose", "marketCap",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "regularMarketVolume",
)


def _settings(config: dict | None) -> dict:
    if not isinstance(config, dict):
        return {}
    return config.get("opportunity", config)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _clean(value: Any) -> Any:
    """Make upstream numpy/NaN/date values strict JSON, without inventing data."""
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if hasattr(value, "item"):
        return _clean(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _timestamp(value: Any) -> datetime | None:
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _eligible_quote(quote: Any, settings: dict) -> bool:
    if not isinstance(quote, dict):
        return False
    symbol = quote.get("symbol")
    cap = _number(quote.get("marketCap"))
    volume = _number(quote.get("averageDailyVolume3Month"))
    return bool(
        isinstance(symbol, str) and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol)
        and quote.get("quoteType") == "EQUITY"
        and quote.get("currency") == "USD"
        and quote.get("exchange") in _EXCHANGES
        and cap is not None and cap >= settings.get("min_market_cap", 5_000_000_000)
        and volume is not None and volume >= settings.get("min_average_volume", 300_000)
    )


def _screen_page(response: Any, offset: int | None = None) -> tuple[list, int]:
    if not isinstance(response, dict) or not isinstance(response.get("quotes"), list):
        raise ValueError("Yahoo screener did not return a quotes list")
    total = _number(response.get("total"))
    if total is None or total < 0 or not total.is_integer():
        raise ValueError("Yahoo screener did not return a valid total")
    if offset is not None and response.get("start") not in (None, offset):
        raise ValueError("Yahoo screener returned the wrong page offset")
    if any(not isinstance(quote, dict) or not quote.get("symbol") for quote in response["quotes"]):
        raise ValueError("Yahoo screener returned malformed quote records")
    return response["quotes"], int(total)


def discover_universe(config: dict | None = None) -> dict:
    """Find a fresh, deduplicated universe; expose partial coverage explicitly.

    ``complete`` describes the requested capped screen. ``capped`` and
    ``total_available`` disclose the eligible companies outside that scope.
    The predefined fallback is always partial even when its own requests work.
    """
    settings = _settings(config)
    limit = max(1, min(int(settings.get("universe_limit", 750)), 2_000))
    query = yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["region", "us"]),
        yf.EquityQuery("is-in", ["exchange", *sorted(_EXCHANGES)]),
        yf.EquityQuery("gte", ["intradaymarketcap", settings.get("min_market_cap", 5_000_000_000)]),
        yf.EquityQuery("gte", ["avgdailyvol3m", settings.get("min_average_volume", 300_000)]),
    ])
    quotes: dict[str, dict] = {}
    errors: list[str] = []
    total = 0
    offset = 0
    complete = True
    source = "Yahoo Finance custom equity screener"
    seen_page_symbols: set[str] = set()
    try:
        while offset < limit:
            page, current_total = _screen_page(yf.screen(
                query, offset=offset, size=min(250, limit - offset),
                sortField="intradaymarketcap", sortAsc=False, session=_SESSION,
            ), offset)
            total = max(total, current_total)
            if not page:
                if offset < min(limit, current_total):
                    raise ValueError("Yahoo screener returned an empty page before its reported total")
                break
            page_symbols = {quote["symbol"] for quote in page}
            if page_symbols <= seen_page_symbols:
                raise ValueError("Yahoo screener repeated a prior page")
            seen_page_symbols.update(page_symbols)
            for quote in page:
                if _eligible_quote(quote, settings):
                    quotes[quote["symbol"]] = _clean(quote)
            offset += len(page)
            if offset >= current_total:
                break
    except Exception as exc:
        complete = False
        errors.append(f"Custom universe: {sanitize_error(exc)}")
        source += "; partial predefined-screen fallback"
        # Predefined GET screens also work when Yahoo rejects custom POSTs.
        # They cannot be treated as broad or market-cap-ranked coverage.
        for name in _FALLBACK_SCREENS:
            try:
                page, _ = _screen_page(yf.screen(name, count=250, session=_SESSION))
                for quote in page:
                    if _eligible_quote(quote, settings):
                        quotes[quote["symbol"]] = _clean(quote)
            except Exception as fallback_exc:
                errors.append(f"Fallback {name}: {sanitize_error(fallback_exc)}")
    ordered = sorted(quotes.values(), key=lambda quote: quote.get("marketCap", 0), reverse=True)[:limit]
    if not ordered:
        complete = False
        errors.append("No eligible USD common-equity quotes were returned")
    return {
        "quotes": ordered,
        "source": source,
        "total_available": total,
        "complete": complete,
        "capped": total > limit,
        "requested_limit": limit,
        "scope": f"Largest {limit} eligible US-listed equities by market capitalization" if complete else "Partial live US-listed equity discovery",
        "errors": errors,
    }


def normalize_cashflows(frame: Any) -> list[dict]:
    """Preserve reported fiscal periods and missing SBC; never assume zero."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    fields = {
        "operating_cash_flow": ("Operating Cash Flow", "Total Cash From Operating Activities"),
        "capital_expenditure": ("Capital Expenditure", "Capital Expenditures"),
        "free_cash_flow": ("Free Cash Flow",),
        "stock_based_compensation": ("Stock Based Compensation",),
    }
    rows = {}
    for column in frame.columns:
        period = _timestamp(column.to_pydatetime() if hasattr(column, "to_pydatetime") else column)
        if period is None:
            continue
        record = {"date": period.date().isoformat()}
        for target, aliases in fields.items():
            record[target] = next(
                (_number(frame.at[label, column]) for label in aliases if label in frame.index), None
            )
        # A Yahoo all-NaN column is not a reported financial period.
        if any(record[key] is not None for key in fields):
            rows[record["date"]] = record
    return sorted(rows.values(), key=lambda row: row["date"], reverse=True)


def normalize_history(frame: Any) -> list[dict]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    rows = {}
    for index, row in frame.iterrows():
        try:
            # Preserve the exchange-local session date, not a UTC-shifted date.
            session_date = pd.Timestamp(index).date().isoformat()
        except (TypeError, ValueError):
            continue
        close, high = _number(row.get("Close")), _number(row.get("High"))
        if close is None or high is None or close <= 0 or high <= 0:
            continue
        rows[session_date] = {
            "date": session_date, "close": close, "high": high,
            "volume": _number(row.get("Volume")),
        }
    return sorted(rows.values(), key=lambda row: row["date"])


def normalize_news(items: Any, now: datetime | None = None, max_age_days: int = 7, limit: int = 4) -> list[dict]:
    now = _timestamp(now) or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    results = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        content = item.get("content", item)
        if not isinstance(content, dict):
            continue
        title = content.get("title")
        canonical = content.get("canonicalUrl")
        clickthrough = content.get("clickThroughUrl")
        url = (canonical.get("url") if isinstance(canonical, dict) else None) or (
            clickthrough.get("url") if isinstance(clickthrough, dict) else None
        ) or content.get("link")
        published = _timestamp(content.get("pubDate") or content.get("providerPublishTime"))
        if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
            continue
        try:
            parsed = urlparse(url)
            valid_url = parsed.scheme in ("http", "https") and bool(parsed.netloc) and not parsed.username and not parsed.password
        except ValueError:
            valid_url = False
        if not valid_url or published is None or not cutoff <= published <= now + timedelta(minutes=5):
            continue
        provider = content.get("provider")
        publisher = provider.get("displayName") if isinstance(provider, dict) else content.get("publisher")
        results[url] = {
            "title": title.strip(), "url": url, "published_at": published.isoformat(),
            "publisher": publisher if isinstance(publisher, str) else None,
        }
    return sorted(results.values(), key=lambda row: row["published_at"], reverse=True)[:limit]


def _can_reuse_fundamentals(cached: Any, quote: dict, settings: dict, now: datetime) -> bool:
    if not isinstance(cached, dict) or not isinstance(cached.get("info"), dict):
        return False
    if _timestamp(quote.get("regularMarketTime")) is None or not _number(quote.get("regularMarketPrice")):
        return False
    fetched = _timestamp(cached.get("fundamentals_fetched_at"))
    max_hours = max(0, min(float(settings.get("fundamental_cache_hours", 24)), 24))
    if fetched is None or not timedelta(0) <= now - fetched < timedelta(hours=max_hours):
        return False
    if cached.get("fundamentals_errors") or not cached.get("annual_cashflows") or not cached.get("quarterly_cashflows"):
        return False
    threshold = float(settings.get("cache_refresh_move_pct", 4))
    daily_move = _number(quote.get("regularMarketChangePercent"))
    old_price = _number(cached["info"].get("currentPrice"))
    new_price = _number(quote.get("regularMarketPrice"))
    if daily_move is not None and abs(daily_move) >= threshold:
        return False
    if old_price and new_price and abs(new_price / old_price - 1) * 100 >= threshold:
        return False
    for data in (cached["info"], quote):
        for field in ("earningsTimestamp", "earningsTimestampStart", "earningsTimestampEnd"):
            earnings = _timestamp(data.get(field))
            if earnings is not None and fetched < earnings <= now:
                return False
    return True


def fetch_snapshot(ticker: str, quote: dict | None = None, cached: dict | None = None, config: dict | None = None, now: datetime | None = None) -> dict:
    """Fetch independently timestamped quotes, cash flows, price history, news.

    Individual failures stay on this stock. A missing timestamp is never replaced
    with the retrieval time: downstream freshness gates must reject that quote.
    """
    settings = _settings(config)
    now = _timestamp(now) or datetime.now(timezone.utc)
    quote = _clean(quote) if isinstance(quote, dict) else {}
    if quote.get("symbol") not in (None, ticker):
        quote = {}
    snapshot = {
        "ticker": ticker, "info": {}, "quote_as_of": None, "fetched_at": now.isoformat(),
        "fundamentals_fetched_at": None, "annual_cashflows": [], "quarterly_cashflows": [],
        "history": [], "history_adjusted": False, "news": [], "errors": [],
        "fundamentals_errors": [],
    }
    try:
        stock = yf.Ticker(ticker, session=_SESSION)
    except Exception as exc:
        snapshot["errors"].append(f"Ticker: {sanitize_error(exc)}")
        return snapshot
    use_cache = _can_reuse_fundamentals(cached, quote, settings, now)
    if use_cache:
        for key in ("info", "annual_cashflows", "quarterly_cashflows", "fundamentals_fetched_at"):
            snapshot[key] = _clean(cached[key])
    else:
        try:
            info = stock.get_info()
            if not isinstance(info, dict) or not info:
                raise ValueError("Yahoo returned no company information")
            snapshot["info"] = _clean(info)
        except Exception as exc:
            snapshot["fundamentals_errors"].append(f"Company information: {sanitize_error(exc)}")
        for key, frequency in (("annual_cashflows", "yearly"), ("quarterly_cashflows", "quarterly")):
            try:
                rows = normalize_cashflows(stock.get_cash_flow(freq=frequency, pretty=True))
                if not rows:
                    raise ValueError("Yahoo returned no reported cash-flow periods")
                snapshot[key] = rows
            except Exception as exc:
                snapshot["fundamentals_errors"].append(f"{key}: {sanitize_error(exc)}")
        snapshot["fundamentals_fetched_at"] = now.isoformat()
    # Even when fundamentals are cached, obtain a fresh quote if discovery did
    # not supply one. Do not retain yesterday's market fields in this branch.
    quote_time = _timestamp(quote.get("regularMarketTime"))
    quote_price = _number(quote.get("regularMarketPrice"))
    fresh_quote = quote_time is not None and quote_price is not None and quote_price > 0
    if not fresh_quote:
        try:
            fresh_info = stock.get_info()
            quote = {key: fresh_info[key] for key in _QUOTE_FIELDS if key in fresh_info}
            if "regularMarketPrice" not in quote:
                quote["regularMarketPrice"] = fresh_info.get("currentPrice")
        except Exception as exc:
            snapshot["errors"].append(f"Current quote: {sanitize_error(exc)}")
            quote = {}
    # Clear cached market fields before merging. Missing new data must remain
    # missing, not look as if it belongs to the latest quote timestamp.
    for key in _MARKET_FIELDS:
        snapshot["info"].pop(key, None)
    snapshot["info"].pop("currentPrice", None)
    snapshot["info"].update({key: _clean(quote[key]) for key in _QUOTE_FIELDS if key in quote})
    snapshot["info"]["currentPrice"] = _number(quote.get("regularMarketPrice"))
    # Query identity is authoritative, but do not infer exchange/currency or
    # financial statement currency for missing responses.
    snapshot["info"]["symbol"] = ticker
    quote_time = _timestamp(quote.get("regularMarketTime"))
    if quote_time is not None and quote_time <= now + timedelta(minutes=5):
        snapshot["quote_as_of"] = quote_time.isoformat()
    else:
        snapshot["errors"].append("Current quote has no valid source timestamp")
    if not snapshot["info"].get("currentPrice"):
        snapshot["errors"].append("Current quote has no positive price")
    try:
        # Yahoo's raw Close/High are split-adjusted but not dividend-adjusted.
        # This avoids comparing a dividend-adjusted historical high to raw price.
        snapshot["history"] = normalize_history(stock.history(period="1y", auto_adjust=False, timeout=12))
        snapshot["history_adjusted"] = bool(snapshot["history"])
        if not snapshot["history"]:
            raise ValueError("Yahoo returned no valid daily price history")
    except Exception as exc:
        snapshot["errors"].append(f"Price history: {sanitize_error(exc)}")
    try:
        snapshot["news"] = normalize_news(
            stock.get_news(count=10), now=now,
            max_age_days=int(settings.get("news_max_age_days", 7)),
            limit=int(settings.get("news_limit", 4)),
        )
    except Exception as exc:
        snapshot["errors"].append(f"Recent headlines: {sanitize_error(exc)}")
    snapshot["errors"].extend(snapshot["fundamentals_errors"])
    return _clean(snapshot)
