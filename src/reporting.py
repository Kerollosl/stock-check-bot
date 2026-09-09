import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
import pandas_market_calendars as market_calendars

from .indicators.fundamental import FundamentalAnalyzer
from .indicators.macro import MacroAnalyzer
from .indicators.technical import TechnicalAnalyzer
from .scoring.weighted_scorer import WeightedScorer
from .utils.data_fetcher import DataFetcher
from .utils.errors import sanitize_error


DIP_LABELS = {
    "daily_dip": "daily dip",
    "weekly_dip": "weekly dip",
    "major_dip_from_high": "52-week drawdown",
}


def _iso_timestamp(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _dip_kwargs(config: dict) -> dict:
    dip_config = config.get("dip_detection", {})
    return {
        "daily_threshold": dip_config.get("daily_drop_pct", -3.0),
        "weekly_threshold": dip_config.get("weekly_drop_pct", -7.0),
        "from_high_threshold": dip_config.get("from_high_pct", -15.0),
        "lookback": dip_config.get("lookback_days", 252),
    }


def latest_completed_market_session(now: datetime | None = None) -> date:
    """Return the latest NYSE session whose close is safely in the past."""
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    calendar = market_calendars.get_calendar("NYSE")
    schedule = calendar.schedule(
        start_date=(now - timedelta(days=14)).date(),
        end_date=now.date(),
    )
    completed = schedule[schedule["market_close"] <= now - timedelta(minutes=15)]
    if completed.empty:
        raise RuntimeError("No completed NYSE session found in the last 14 days")
    return completed.index[-1].date()


def _date_from_timestamp(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _apply_source_freshness(
    sources: dict[str, dict],
    expected_market_session: date,
    generated_at: datetime,
):
    for source in ("spy", "vix"):
        details = sources.get(source, {})
        if details.get("status") != "ok" or not details.get("as_of"):
            continue
        if _date_from_timestamp(details["as_of"]) < expected_market_session:
            details.update(
                {
                    "status": "stale",
                    "message": (
                        f"Expected market session {expected_market_session.isoformat()}, "
                        f"received {details['as_of'][:10]}"
                    ),
                }
            )

    freshness_limits = {"treasury_yields": 7, "fred_funds": 70}
    for source, maximum_age_days in freshness_limits.items():
        details = sources.get(source, {})
        if details.get("status") != "ok" or not details.get("as_of"):
            continue
        age_days = (generated_at.date() - _date_from_timestamp(details["as_of"])).days
        if age_days > maximum_age_days:
            details.update(
                {
                    "status": "stale",
                    "message": (
                        f"Latest observation is {age_days} days old; "
                        f"maximum allowed is {maximum_age_days}"
                    ),
                }
            )


def build_report(tickers: list[str], config_path: str = "config.yaml") -> dict:
    """Build a machine-readable watchlist report with source-health metadata."""
    with open(config_path) as config_file:
        config = yaml.safe_load(config_file)

    generated_at = datetime.now(timezone.utc)
    expected_session = latest_completed_market_session(generated_at)
    fetcher = DataFetcher()
    scorer = WeightedScorer(config_path)
    macro = MacroAnalyzer(fetcher)
    macro_scores = macro.get_all_scores()
    sources = macro.get_data_health()
    _apply_source_freshness(sources, expected_session, generated_at)
    errors = []
    warnings = []

    for source, details in sources.items():
        if details["status"] != "ok":
            errors.append(
                {
                    "scope": source,
                    "message": details.get("message", "Source unavailable"),
                }
            )

    stocks = []
    for ticker in tickers:
        ticker = ticker.upper()
        try:
            history = fetcher.get_stock_history(ticker, period="2y")
            if history.empty or len(history) < 50:
                raise ValueError(f"Insufficient price history for {ticker}")

            technical = TechnicalAnalyzer(history)
            technical_scores = technical.get_all_scores()
            dips = technical.detect_dips(**_dip_kwargs(config))

            history_as_of = _iso_timestamp(history.index[-1])
            history_status = "ok"
            history_error = None
            if _date_from_timestamp(history_as_of) < expected_session:
                history_status = "stale"
                history_error = (
                    f"Expected market session {expected_session.isoformat()}, "
                    f"received {history_as_of[:10]}"
                )
                errors.append({"scope": ticker, "message": history_error})

            fundamental_error = None
            missing_fundamentals = []
            try:
                earnings = fetcher.get_earnings(ticker)
                fundamental_scores = FundamentalAnalyzer(earnings).get_all_scores()
                missing_fundamentals = [
                    field
                    for field in ("pe_ratio", "revenue_growth", "earnings_growth")
                    if earnings.get(field) is None
                ]
                if missing_fundamentals:
                    warnings.append(
                        {
                            "scope": ticker,
                            "message": "Missing fundamental fields: "
                            + ", ".join(missing_fundamentals),
                        }
                    )
            except Exception as error:
                fundamental_error = sanitize_error(error)
                fundamental_scores = {
                    "earnings_surprise": 0.5,
                    "pe_ratio": 0.5,
                    "revenue_growth": 0.5,
                    "earnings_growth": 0.5,
                }
                errors.append({"scope": ticker, "message": fundamental_error})

            result = scorer.compute_composite(
                technical_scores,
                fundamental_scores,
                macro_scores,
            )
            price = float(history["Close"].iloc[-1])
            previous_close = float(history["Close"].iloc[-2])
            high_52w = float(history["Close"].tail(252).max())

            macro_degraded = any(
                details["status"] != "ok" for details in sources.values()
            )
            if history_status != "ok" or fundamental_error or macro_degraded:
                stock_status = "degraded"
            elif missing_fundamentals:
                stock_status = "warning"
            else:
                stock_status = "ok"

            stock = {
                "ticker": ticker,
                "status": stock_status,
                "as_of": history_as_of,
                "price": round(price, 2),
                "change_pct": round((price / previous_close - 1) * 100, 2),
                "high_52w": round(high_52w, 2),
                "composite_score": result["composite_score"],
                "signal": result["signal"],
                "confidence": result["confidence"],
                "technical": {
                    key: round(float(value), 4)
                    for key, value in technical_scores.items()
                },
                "fundamental": {
                    key: round(float(value), 4)
                    for key, value in fundamental_scores.items()
                },
                "macro": {
                    key: round(float(value), 4)
                    for key, value in macro_scores.items()
                },
                "dips": {
                    key: value.item() if hasattr(value, "item") else value
                    for key, value in dips.items()
                },
                "data_health": {
                    "price_history": {
                        "status": history_status,
                        "as_of": history_as_of,
                        **({"message": history_error} if history_error else {}),
                    },
                    "fundamentals": {
                        "status": "error"
                        if fundamental_error
                        else "warning"
                        if missing_fundamentals
                        else "ok",
                        **(
                            {"message": fundamental_error}
                            if fundamental_error
                            else {
                                "message": "Missing fields: "
                                + ", ".join(missing_fundamentals)
                            }
                            if missing_fundamentals
                            else {}
                        ),
                    },
                },
            }
            if fundamental_error:
                stock["error"] = fundamental_error
            stocks.append(stock)
        except Exception as error:
            message = sanitize_error(error)
            stocks.append({"ticker": ticker, "status": "error", "error": message})
            errors.append({"scope": ticker, "message": message})

    status = "degraded" if errors else "warning" if warnings else "ok"
    return {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "status": status,
        "expected_market_session": expected_session.isoformat(),
        "watchlist": [ticker.upper() for ticker in tickers],
        "thresholds": {
            "score_delta": 0.05,
            **config.get("dip_detection", {}),
        },
        "macro": {
            "scores": {
                key: round(float(value), 4)
                for key, value in macro_scores.items()
            },
            "overall": round(sum(macro_scores.values()) / len(macro_scores), 4),
        },
        "sources": sources,
        "stocks": stocks,
        "warnings": warnings,
        "errors": errors,
    }


def compare_reports(current: dict, previous: dict | None, score_delta: float = 0.05) -> dict:
    """Find changes worth notifying about between two structured reports."""
    events = []
    baseline = previous is None

    previous_errors = {
        (error.get("scope"), error.get("message"))
        for error in (previous or {}).get("errors", [])
    }
    for error in current.get("errors", []):
        error_key = (error.get("scope"), error.get("message"))
        if error_key not in previous_errors:
            events.append(
                {
                    "type": "data_error",
                    "scope": error.get("scope", "report"),
                    "message": error.get("message", "Data source degraded"),
                }
            )

    previous_warnings = {
        (warning.get("scope"), warning.get("message"))
        for warning in (previous or {}).get("warnings", [])
    }
    for warning in current.get("warnings", []):
        warning_key = (warning.get("scope"), warning.get("message"))
        if warning_key not in previous_warnings:
            events.append(
                {
                    "type": "data_warning",
                    "scope": warning.get("scope", "report"),
                    "message": warning.get("message", "Data is incomplete"),
                }
            )

    if previous is not None:
        previous_sources = previous.get("sources", {})
        for source, details in current.get("sources", {}).items():
            old_status = previous_sources.get(source, {}).get("status")
            new_status = details.get("status")
            if old_status and new_status != old_status:
                events.append(
                    {
                        "type": "source_status_changed",
                        "scope": source,
                        "message": f"Source status changed from {old_status} to {new_status}",
                    }
                )

        previous_stocks = {
            stock["ticker"]: stock for stock in previous.get("stocks", [])
        }
        current_stocks = {
            stock["ticker"]: stock for stock in current.get("stocks", [])
        }

        for ticker, stock in current_stocks.items():
            old_stock = previous_stocks.get(ticker)
            if old_stock is None:
                events.append(
                    {
                        "type": "ticker_added",
                        "scope": ticker,
                        "message": f"{ticker} was added to the watchlist",
                    }
                )
                continue

            old_status = old_stock.get("status", "ok")
            new_status = stock.get("status", "ok")
            old_local_health = old_stock.get("data_health", {})
            old_local_problem = old_status == "error" or any(
                details.get("status") != "ok"
                for details in old_local_health.values()
            )
            if new_status == "ok" and old_status != "ok" and old_local_problem:
                events.append(
                    {
                        "type": "ticker_data_recovered",
                        "scope": ticker,
                        "message": f"{ticker} data status recovered from {old_status} to ok",
                    }
                )

            old_price_health = old_local_health.get("price_history", {}).get(
                "status",
                "ok" if old_status in ("ok", "warning") else "error",
            )
            new_price_health = stock.get("data_health", {}).get(
                "price_history", {}
            ).get(
                "status",
                "ok" if new_status in ("ok", "warning") else "error",
            )
            if old_price_health == "ok" and new_price_health == "ok":
                old_dips = old_stock.get("dips", {})
                new_dips = stock.get("dips", {})
                for key, label in DIP_LABELS.items():
                    if new_dips.get(key) and not old_dips.get(key):
                        events.append(
                            {
                                "type": "dip_started",
                                "scope": ticker,
                                "message": f"{ticker} triggered a new {label}",
                            }
                        )

            if new_status == "ok" and old_status == "ok":
                old_signal = old_stock.get("signal")
                new_signal = stock.get("signal")
                if old_signal and new_signal and old_signal != new_signal:
                    events.append(
                        {
                            "type": "signal_changed",
                            "scope": ticker,
                            "message": f"{ticker} signal changed from {old_signal} to {new_signal}",
                        }
                    )

                old_score = old_stock.get("composite_score")
                new_score = stock.get("composite_score")
                if old_score is not None and new_score is not None:
                    delta = new_score - old_score
                    if abs(delta) >= score_delta:
                        events.append(
                            {
                                "type": "score_moved",
                                "scope": ticker,
                                "message": (
                                    f"{ticker} score moved {delta:+.3f} "
                                    f"from {old_score:.3f} to {new_score:.3f}"
                                ),
                            }
                        )

        for ticker in sorted(previous_stocks.keys() - current_stocks.keys()):
            events.append(
                {
                    "type": "ticker_removed",
                    "scope": ticker,
                    "message": f"{ticker} was removed from the watchlist",
                }
            )

    return {
        "baseline": baseline,
        "score_delta": score_delta,
        "notification_required": bool(events),
        "events": events,
    }


def render_markdown(report: dict, comparison: dict) -> str:
    """Render a concise human-readable report for GitHub and artifacts."""
    generated_at = report["generated_at"]
    market_dates = [
        stock["as_of"][:10]
        for stock in report.get("stocks", [])
        if stock.get("as_of")
    ]
    report_date = max(market_dates) if market_dates else generated_at[:10]
    lines = [
        f"# Stock Check — {report_date}",
        "",
        f"**Report status:** {report['status'].upper()}  ",
        f"**Generated:** {generated_at}  ",
        f"**Macro score:** {report['macro']['overall']:.2f}",
        "",
        "## Meaningful changes",
        "",
    ]

    events = comparison.get("events", [])
    if events:
        lines.extend(f"- {event['message']}" for event in events)
    elif comparison.get("baseline"):
        lines.append("- Initial baseline created; no prior run was available for comparison.")
    else:
        lines.append("- No signal changes, new dip triggers, large score moves, or data failures.")

    lines.extend(
        [
            "",
            "## Watchlist",
            "",
            "| Ticker | As of | Price | Day | Score | Signal | Confidence | Active dips |",
            "|---|---:|---:|---:|---:|---|---|---|",
        ]
    )
    for stock in report.get("stocks", []):
        if stock.get("status") == "error":
            lines.append(
                f"| {stock['ticker']} | — | — | — | — | DATA ERROR | — | {stock['error']} |"
            )
            continue

        dips = stock.get("dips", {})
        active_dips = [label for key, label in DIP_LABELS.items() if dips.get(key)]
        lines.append(
            "| {ticker} | {as_of} | ${price:,.2f} | {change:+.2f}% | {score:.3f} | "
            "{signal} | {confidence} | {dips} |".format(
                ticker=stock["ticker"],
                as_of=stock["as_of"][:10],
                price=stock["price"],
                change=stock["change_pct"],
                score=stock["composite_score"],
                signal=stock["signal"],
                confidence=stock["confidence"],
                dips=", ".join(active_dips) if active_dips else "—",
            )
        )

    lines.extend(["", "## Data health", ""])
    for source, details in report.get("sources", {}).items():
        line = f"- **{source}:** {details['status']}"
        if details.get("as_of"):
            line += f" (as of {details['as_of'][:10]})"
        if details.get("message"):
            line += f" — {details['message']}"
        lines.append(line)

    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(
            f"- **{warning['scope']}:** {warning['message']}"
            for warning in report["warnings"]
        )

    lines.extend(
        [
            "",
            "---",
            "_Automated monitoring output, not financial advice. No trades were placed._",
            "",
        ]
    )
    return "\n".join(lines)


def load_previous_report(path: str | None) -> tuple[dict | None, str | None]:
    if not path:
        return None, None
    report_path = Path(path)
    if not report_path.exists():
        return None, None
    try:
        with report_path.open() as report_file:
            report = json.load(report_file)
    except (OSError, json.JSONDecodeError) as error:
        return None, f"Previous report could not be read: {sanitize_error(error)}"

    valid_stocks = (
        isinstance(report, dict)
        and isinstance(report.get("stocks"), list)
        and all(
            isinstance(stock, dict) and isinstance(stock.get("ticker"), str)
            for stock in report["stocks"]
        )
    )
    if not valid_stocks or report.get("schema_version") != 1:
        return None, "Previous report has an unsupported or invalid schema"
    return report, None


def write_json(path: str, value: dict):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as output_file:
        json.dump(value, output_file, indent=2, sort_keys=True)
        output_file.write("\n")


def write_text(path: str, value: str):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(value)
