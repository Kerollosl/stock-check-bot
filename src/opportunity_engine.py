"""Conservative, deterministic research screening from dated financial snapshots.

The valuation is a sensitivity model of equity cash flow, not an appraisal or a
price target. Nothing in this module fetches data or recommends a transaction.
"""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from math import isfinite
from statistics import median
from urllib.parse import quote


DEFAULTS = {
    "min_market_cap": 5_000_000_000,
    "min_average_daily_dollar_volume": 20_000_000,
    "min_operating_margin": 0.10,
    "min_revenue_growth": 0.0,
    "max_net_debt_to_ebitda": 3.0,
    "min_drawdown_pct": 20.0,
    "min_margin_of_safety_pct": 25.0,
    "max_stress_downside_pct": 35.0,
    "max_fundamental_age_days": 150,
}
US_EXCHANGES = {
    "NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "NAS", "NYS",
    "NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM", "NYSE", "NYSEARCA",
    "NYSEAMERICAN", "CBOE", "BATS",
}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(value):
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _date(value):
    parsed = _timestamp(value)
    return parsed.date() if parsed else None


def _records(raw, count, minimum_gap, maximum_gap):
    """Reject conflicting duplicates and gaps; do not mistake YTD for quarters."""
    if not isinstance(raw, list):
        return None, "Cash-flow statements are missing."
    records = {}
    fields = ("operating_cash_flow", "capital_expenditure", "stock_based_compensation")
    for item in raw:
        if not isinstance(item, dict):
            continue
        period = _date(item.get("date"))
        if period is None:
            continue
        values = tuple(_number(item.get(field)) for field in fields)
        if period in records and records[period] != values:
            return None, "Conflicting cash-flow statements share the same fiscal date."
        records[period] = values
    selected = sorted(records.items(), reverse=True)[:count]
    if len(selected) < count:
        return None, f"Need {count} distinct cash-flow periods; received {len(selected)}."
    for (newer, _), (older, _) in zip(selected, selected[1:]):
        if not minimum_gap <= (newer - older).days <= maximum_gap:
            return None, "Cash-flow periods are not consecutive reporting periods."
    if any(value is None for _, values in selected for value in values):
        return None, "CFO, capital expenditure, and stock compensation must all be reported; missing is not zero."
    return [
        {"date": period, "cfo": values[0], "capex": abs(values[1]),
         "sbc": abs(values[2]), "owner_cash": values[0] - abs(values[1]) - abs(values[2])}
        for period, values in selected
    ], None


def _cash_value(cash_per_share, growth, discount, terminal_growth, haircut=0.0):
    cash = cash_per_share * (1.0 - haircut)
    forecast = sum(cash * (1.0 + growth) ** year / (1.0 + discount) ** year for year in range(1, 6))
    terminal_cash = cash * (1.0 + growth) ** 5 * (1.0 + terminal_growth)
    return forecast + terminal_cash / (discount - terminal_growth) / (1.0 + discount) ** 5


@lru_cache(maxsize=16)
def _market_window(moment):
    # The exchange calendar is bundled local data; there is no network access.
    from pandas_market_calendars import get_calendar
    schedule = get_calendar("NYSE").schedule(
        start_date=(moment - timedelta(days=14)).date(), end_date=moment.date()
    )
    completed = schedule[schedule["market_close"] <= moment - timedelta(minutes=15)]
    last_close = completed["market_close"].iloc[-1].to_pydatetime() if not completed.empty else None
    active = schedule[(schedule["market_open"] <= moment - timedelta(minutes=15))
                      & (schedule["market_close"] > moment - timedelta(minutes=15))]
    return last_close, not active.empty


def _quote_is_fresh(quoted, moment):
    if not quoted or quoted > moment + timedelta(minutes=5):
        return False
    last_close, active_market = _market_window(moment)
    if active_market:
        return (moment - quoted).total_seconds() <= 30 * 60
    # Allow a delayed closing quote but not a morning price labeled today's date.
    return bool(last_close and quoted >= last_close - timedelta(minutes=20))


def evaluate_candidate(snapshot, config=None, now=None):
    """Return a research card. Missing essential inputs always prevent qualification.

    Percent fields have 0..100 units; source Yahoo margins/growth use fractions.
    ``history_adjusted=True`` means prices have been adjusted for stock splits.
    ``fiftyTwoWeekHigh`` from the current quote is an allowed alternative.
    """
    settings = dict(DEFAULTS)
    supplied = config or {}
    if isinstance(supplied, dict):
        supplied = supplied.get("opportunity", supplied)
        for key in DEFAULTS:
            value = _number(supplied.get(key)) if isinstance(supplied, dict) else None
            if value is not None and value >= 0:
                settings[key] = value
    moment = _timestamp(now) if now is not None else datetime.now(timezone.utc)
    if moment is None:
        raise ValueError("now must be an ISO timestamp or datetime")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    info = snapshot.get("info") if isinstance(snapshot.get("info"), dict) else {}
    ticker = str(snapshot.get("ticker") or info.get("symbol") or "UNKNOWN").upper()
    checks, missing, rejected, risks = [], [], [], []
    metrics, valuation = {}, {}

    def check(name, value, passed, detail, structural=False):
        checks.append({"name": name, "passed": passed, "value": value, "detail": detail})
        if passed is None:
            missing.append(detail)
        elif not passed:
            rejected.append((detail, structural))

    def numeric_check(name, key, predicate, detail):
        value = _number(info.get(key))
        check(name, value, None if value is None else predicate(value), detail)
        return value

    currency, financial_currency = info.get("currency"), info.get("financialCurrency")
    check("Comparable USD financials", f"{currency or '?'} / {financial_currency or '?'}",
          None if not currency or not financial_currency else currency == financial_currency == "USD",
          "Quote and financial statements must both be in USD; currency conversion and ADR ratios are not modeled.", True)
    security_type = info.get("quoteType")
    check("Operating-company equity", security_type, None if not security_type else security_type == "EQUITY",
          "This model requires operating-company equity, rather than a fund or other security.", True)
    exchange = str(info.get("exchange") or info.get("fullExchangeName") or "").upper().replace(" ", "")
    check("US listing", exchange or None, None if not exchange else exchange in US_EXCHANGES,
          "A supported US exchange listing is required.", True)
    sector, industry = str(info.get("sector") or ""), str(info.get("industry") or "")
    excluded_industry = any(word in industry.lower() for word in ("bank", "insurance", "reit", "mortgage"))
    excluded_sector = sector.lower() in {"financial services", "financial", "real estate"}
    check("Suitable cash-flow model", industry or sector or None,
          None if not sector or not industry else not (excluded_industry or excluded_sector),
          "Financial companies and real estate require different valuation models and are excluded.", True)
    market_cap = _number(info.get("marketCap"))
    check("Company size", market_cap, None if market_cap is None else market_cap >= settings["min_market_cap"],
          f"Market value must be at least ${settings['min_market_cap'] / 1e9:g} billion.", True)
    price = _number(info.get("regularMarketPrice"))
    if price is None:
        price = _number(info.get("currentPrice"))
    check("Usable price", price, True if price is not None and price > 0 else None,
          "A current, positive market price is required.")
    if price is not None and price <= 0:
        price = None
    quoted = _timestamp(snapshot.get("quote_as_of"))
    quote_fresh = _quote_is_fresh(quoted, moment)
    check("Fresh quote", quoted.isoformat() if quoted else None, True if quote_fresh else None,
          "Quote must be current within 30 minutes during trading, or near the latest NYSE close outside trading, and not future-dated.")
    fetched = _timestamp(snapshot.get("fetched_at"))
    check("Fresh snapshot", fetched.isoformat() if fetched else None,
          True if fetched and -300 <= (moment - fetched).total_seconds() <= 26 * 3600 else None,
          "The financial snapshot must have been fetched within the past 26 hours.")
    if "fundamentals_fetched_at" in snapshot:
        financial_fetch = _timestamp(snapshot.get("fundamentals_fetched_at"))
        check("Fresh financial cache", financial_fetch.isoformat() if financial_fetch else None,
              True if financial_fetch and -300 <= (moment - financial_fetch).total_seconds() <= 26 * 3600 else None,
              "Cached financial inputs must have been fetched within the past 26 hours.")

    history = snapshot.get("history") if isinstance(snapshot.get("history"), list) else []
    dated_history = sorted([
        (period, item) for item in history if isinstance(item, dict)
        and (period := _date(item.get("date"))) is not None
        and moment.date() - timedelta(days=366) <= period <= moment.date()
    ], key=lambda row: row[0])
    volume = _number(info.get("averageDailyVolume3Month"))
    if volume is None:
        volume = _number(info.get("averageVolume"))
    if volume is None:
        volumes = [_number(item.get("volume")) for _, item in dated_history[-63:]]
        valid_volumes = [value for value in volumes if value is not None and value >= 0]
        volume = sum(valid_volumes) / len(valid_volumes) if len(valid_volumes) >= 20 else None
    dollar_volume = volume * price if volume is not None and price is not None else None
    check("Trading liquidity", dollar_volume,
          None if dollar_volume is None else dollar_volume >= settings["min_average_daily_dollar_volume"],
          f"Average daily trading value must be at least ${settings['min_average_daily_dollar_volume'] / 1e6:g} million.", True)
    metrics["average_daily_dollar_volume"] = dollar_volume
    shares = _number(info.get("sharesOutstanding"))
    comparable_shares = bool(shares and shares > 0 and market_cap and price and abs(shares * price / market_cap - 1.0) <= 0.25)
    check("Consistent share units", shares, True if comparable_shares else None,
          "Current shares × price must agree with market value within 25%; split and ADR mismatches block valuation.")

    margin = numeric_check("Profitable business", "profitMargins", lambda x: x > 0, "Reported net profit margin must be positive.")
    operating_margin = numeric_check("Operating profitability", "operatingMargins", lambda x: x >= settings["min_operating_margin"],
                                     f"Reported operating margin must be at least {settings['min_operating_margin']:.0%}.")
    growth = numeric_check("Revenue resilience", "revenueGrowth", lambda x: x >= settings["min_revenue_growth"],
                          f"Latest reported year-over-year revenue growth must be at least {settings['min_revenue_growth']:.0%}.")
    metrics.update({"net_margin_pct": margin * 100 if margin is not None else None,
                    "operating_margin_pct": operating_margin * 100 if operating_margin is not None else None,
                    "revenue_growth_pct": growth * 100 if growth is not None else None})
    debt, cash, ebitda = (_number(info.get(key)) for key in ("totalDebt", "totalCash", "ebitda"))
    leverage = (debt - cash) / ebitda if debt is not None and debt >= 0 and cash is not None and cash >= 0 and ebitda is not None and ebitda > 0 else None
    check("Balance-sheet capacity", leverage, None if leverage is None else leverage <= settings["max_net_debt_to_ebitda"],
          f"Net debt / EBITDA must be at most {settings['max_net_debt_to_ebitda']:g}; missing debt, cash or positive EBITDA blocks qualification.")
    metrics["net_debt_to_ebitda"] = leverage

    annual, annual_error = _records(snapshot.get("annual_cashflows"), 3, 330, 400)
    quarters, quarter_error = _records(snapshot.get("quarterly_cashflows"), 4, 70, 110)
    if quarters and not 250 <= (quarters[0]["date"] - quarters[-1]["date"]).days <= 310:
        quarters, quarter_error = None, "Four quarters must span approximately nine months between their fiscal ends."
    check("Three annual statements", len(annual) if annual else None, True if annual else None, annual_error or "Three consecutive fiscal years have complete cash-flow inputs.")
    check("Four quarterly statements", len(quarters) if quarters else None, True if quarters else None, quarter_error or "Four consecutive nonduplicate quarters have complete cash-flow inputs.")
    latest_period = quarters[0]["date"] if quarters else None
    if latest_period:
        fresh_fundamentals = 0 <= (moment.date() - latest_period).days <= settings["max_fundamental_age_days"]
        if annual:
            fresh_fundamentals = fresh_fundamentals and 0 <= (latest_period - annual[0]["date"]).days <= 370
        check("Current fiscal statements", latest_period.isoformat(), True if fresh_fundamentals else None,
              f"Latest fiscal period must be no more than {settings['max_fundamental_age_days']:g} days old; annual statements must align with it.")
    metrics["latest_fiscal_period"] = latest_period.isoformat() if latest_period else None
    normal_cash = None
    if annual and quarters:
        annual_owner_cash = [item["owner_cash"] for item in annual]
        ttm_cash = sum(item["owner_cash"] for item in quarters)
        ttm_cfo = sum(item["cfo"] for item in quarters)
        ttm_capex = sum(item["capex"] for item in quarters)
        ttm_sbc = sum(item["sbc"] for item in quarters)
        normal_cash = min(ttm_cash, median(annual_owner_cash))
        check("Durable owner cash flow", {"annual": annual_owner_cash, "ttm": ttm_cash},
              all(value > 0 for value in annual_owner_cash) and ttm_cash > 0,
              "Owner cash flow (CFO minus absolute capex minus stock compensation) must be positive in each of three years and in the latest four quarters.")
        metrics.update({"ttm_operating_cash_flow": ttm_cfo, "ttm_capital_expenditure": ttm_capex,
                        "ttm_stock_based_compensation": ttm_sbc, "ttm_owner_cash_flow": ttm_cash,
                        "normalised_annual_owner_cash_flow": normal_cash,
                        "normalized_annual_owner_cash_flow": normal_cash,
                        "annual_owner_cash_flows": annual_owner_cash,
                        "free_cash_flow_yield_pct": normal_cash / market_cap * 100 if market_cap and market_cap > 0 else None,
                        "free_cash_flow_yield_definition": "min(TTM, median of last 3 annual CFO − |capex| − |stock compensation|) / current market value"})
        if ttm_cash < annual_owner_cash[-1] * 0.8:
            risks.append("Latest four-quarter owner cash flow is more than 20% below the oldest annual result; investigate business deterioration.")
            check("Cash-flow resilience", ttm_cash / annual_owner_cash[-1] - 1 if annual_owner_cash[-1] > 0 else None,
                  annual_owner_cash[-1] > 0 and ttm_cash >= annual_owner_cash[-1] * 0.8,
                  "Latest four-quarter owner cash flow must not be more than 20% below the oldest of the three annual results.")

    quote_high = _number(info.get("fiftyTwoWeekHigh"))
    usable_highs = [_number(item.get("high")) or _number(item.get("close")) for _, item in dated_history]
    usable_highs = [value for value in usable_highs if value is not None and value > 0]
    high = quote_high if quote_high and quote_high > 0 else None
    if snapshot.get("history_adjusted") is True and len(usable_highs) >= 200:
        history_high = max(usable_highs)
        if high and abs(history_high / high - 1) > 0.25:
            check("Comparable price history", None, None, "History high disagrees with the quote's 52-week high by over 25%; check stock splits and units.")
        else:
            high = history_high
    elif high is None:
        check("Comparable price history", None, None, "Need a current 52-week high or at least 200 sessions of split-adjusted price history.")
    if high and price and high < price * 0.98:
        check("Plausible 52-week high", high, None, "The 52-week high is below the current price; refresh inconsistent quote data.")
    drawdown = max(0, (1 - price / high) * 100) if price and high else None
    metrics["drawdown_pct"] = drawdown
    metrics["fifty_two_week_high"] = high

    if (normal_cash is not None and normal_cash > 0 and comparable_shares and growth is not None
            and not missing and not any(structural for _, structural in rejected)):
        assumed_growth = min(0.08, max(0, growth))
        per_share_cash = normal_cash / shares
        low = _cash_value(per_share_cash, 0, 0.12, 0, 0.20)
        base = _cash_value(per_share_cash, assumed_growth, 0.10, 0.02)
        high_value = _cash_value(per_share_cash, min(0.08, assumed_growth + 0.02), 0.09, 0.02)
        safety = (1 - price / base) * 100
        stress_downside = max(0, (1 - low / price) * 100)
        entry = base * (1 - settings["min_margin_of_safety_pct"] / 100)
        metrics["margin_of_safety_pct"] = safety
        metrics["stress_downside_pct"] = stress_downside
        valuation = {
            "low": round(low, 2), "base": round(base, 2), "high": round(high_value, 2),
            "entry_price": round(entry, 2), "stress_downside_pct": stress_downside,
            "research_price": round(min(entry,
                high * (1 - settings["min_drawdown_pct"] / 100) if high else entry,
                low / (1 - settings["max_stress_downside_pct"] / 100)
                if settings["max_stress_downside_pct"] < 100 else entry), 2),
            "normalised_annual_owner_cash_flow": normal_cash,
            "normalized_annual_owner_cash_flow": normal_cash,
            "cash_flow_per_share": per_share_cash, "growth_assumption_pct": assumed_growth * 100,
            "forecast_years": 5, "base_discount_rate_pct": 10, "base_terminal_growth_pct": 2,
            "stress_discount_rate_pct": 12, "stress_cash_flow_haircut_pct": 20,
            "assumptions": [
                "Owner cash = operating cash flow − absolute capital expenditure − absolute stock compensation; this is a conservative proxy, not distributable cash.",
                "Annual starting cash is the lower of the latest four-quarter total and the median of the last three annual results.",
                f"Base: 5 years at {assumed_growth:.1%} assumed cash-flow growth, a 10% equity discount rate and 2% terminal growth.",
                "Growth is an assumption capped between 0% and 8% using latest reported revenue growth, not a forecast.",
                "Stress: immediate 20% cash-flow reduction, zero growth, 12% discount rate and zero terminal growth.",
                "Upper sensitivity: up to 2 percentage points more growth (8% cap), 9% discount rate and 2% terminal growth.",
                "Current shares are held constant. CFO already reflects interest expense; net debt is checked separately and is not subtracted again.",
                "These scenario estimates omit acquisitions, financing changes and business-specific capital needs; they are not price targets.",
            ],
        }

    structural_rejections = [reason for reason, structural in rejected if structural]
    quality_rejections = [reason for reason, structural in rejected if not structural]
    discount_passed = (drawdown is not None and drawdown >= settings["min_drawdown_pct"]
                       and metrics.get("margin_of_safety_pct", float("-inf")) >= settings["min_margin_of_safety_pct"]
                       and metrics.get("stress_downside_pct", float("inf")) <= settings["max_stress_downside_pct"])
    if structural_rejections:
        status, first_rejection = "rejected", structural_rejections[0]
    elif missing:
        status, first_rejection = "incomplete", missing[0]
    elif quality_rejections:
        status, first_rejection = "rejected", quality_rejections[0]
    elif discount_passed:
        status, first_rejection = "candidate", None
    else:
        status = "watch"
        gaps = []
        if valuation:
            gaps.append(f"Current price ${price:.2f}; all price rules clear at about ${valuation['research_price']:.2f} or below, if the business inputs hold.")
        if drawdown is not None and drawdown < settings['min_drawdown_pct']:
            gaps.append(f"Drawdown is {drawdown:.1f}% versus the {settings['min_drawdown_pct']:g}% threshold.")
        if metrics.get('margin_of_safety_pct', -100) < settings['min_margin_of_safety_pct']:
            gaps.append(f"Needs a {settings['min_margin_of_safety_pct']:g}% margin below the base estimate.")
        if metrics.get('stress_downside_pct', 100) > settings['max_stress_downside_pct']:
            gaps.append(f"Stress-case downside is {metrics['stress_downside_pct']:.0f}%, above the {settings['max_stress_downside_pct']:g}% limit.")
        first_rejection = " ".join(gaps) or "More comparable financial evidence is needed."
    metrics["quality_checks_passed"] = sum(item["passed"] is True for item in checks)
    metrics["quality_checks_total"] = len(checks)
    risks += quality_rejections + missing
    risks.extend([
        "The cause of the price decline is unverified; a falling price alone does not establish undervaluation.",
        "Financial data is vendor-supplied and may be revised; competitive advantage, management quality and accounting require review.",
        "The stress scenario is not a worst case; permanent capital loss remains possible.",
    ])
    for error in snapshot.get("errors", []) if isinstance(snapshot.get("errors"), list) else []:
        risks.append(f"Source note: {str(error)}")
    news = [item for item in snapshot.get("news", []) if isinstance(item, dict)] if isinstance(snapshot.get("news"), list) else []
    rank = 0.0
    if status in {"candidate", "watch"}:
        # Rank distance to the full research zone; clamping expensive stocks to
        # zero previously made the watchlist alphabetical rather than useful.
        rank = round(100 * valuation.get("research_price", 0) / price, 2) if price else 0
    summary = f"{metrics['quality_checks_passed']} of {len(checks)} quantitative checks passed."
    if operating_margin is not None and normal_cash is not None:
        summary = f"Operating margin {operating_margin:.1%}; revenue growth {growth:.1%}. " if growth is not None else ""
        summary += f"Conservative annual cash generation after capex and stock compensation: ${normal_cash / 1e9:.2f}bn. "
        if annual and all(item['owner_cash'] > 0 for item in annual):
            summary += "Positive owner cash in each of the last three fiscal years. "
        if leverage is not None:
            summary += "Cash exceeds debt." if leverage < 0 else f"Net debt is {leverage:.1f} times EBITDA."
    valuation_summary = "Insufficient comparable data to calculate an equity cash-flow scenario."
    if valuation:
        relation = "below" if metrics['margin_of_safety_pct'] >= 0 else "above"
        valuation_summary = f"Price is {abs(metrics['margin_of_safety_pct']):.1f}% {relation} the ${valuation['base']:.2f} base estimate. That estimate assumes {valuation['growth_assumption_pct']:.1f}% annual cash-flow growth for five years, using a 10% discount rate. The stress case implies {valuation['stress_downside_pct']:.1f}% downside."
    why_now = f"Price is {drawdown:.1f}% below its 52-week high." if drawdown is not None else "Drawdown cannot be verified."
    why_now += " Decline cause is unverified; linked headlines are research leads, not a confirmed explanation."
    source_ticker = quote(ticker, safe="")
    return {
        "ticker": ticker, "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": sector or "Unknown", "industry": industry or "Unknown", "price": price,
        "quote_as_of": quoted.isoformat() if quoted else None, "status": status,
        "latest_fiscal_period": metrics["latest_fiscal_period"], "quality_checks": checks,
        "quality_checks_passed": metrics["quality_checks_passed"], "quality_checks_total": len(checks),
        "metrics": metrics, "valuation": valuation, "why_now": why_now,
        "quality_summary": summary, "valuation_summary": valuation_summary,
        "risks": list(dict.fromkeys(risks)), "next_step": (
            "Read the latest 10-K/10-Q and earnings call: verify the decline cause, cash-flow durability, debt maturities, dilution and competitive position before considering an investment."
            if status == "candidate" else first_rejection or "Continue monitoring for a larger discount."
        ), "first_rejection": first_rejection, "news": news,
        "sources": [{"label": "Quote and company profile", "url": f"https://finance.yahoo.com/quote/{source_ticker}/"},
                    {"label": "Cash-flow statements", "url": f"https://finance.yahoo.com/quote/{source_ticker}/cash-flow/"},
                    {"label": "SEC filings search", "url": f"https://www.sec.gov/edgar/browse/?CIK={source_ticker}&owner=exclude"}],
        "rank": rank, "rank_description": "Relative research priority, not a probability or return prediction.",
    }
