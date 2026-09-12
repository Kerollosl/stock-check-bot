"""Scheduled discovery, research queue, alert lifecycle, and durable scan state."""

import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import click
import pandas_market_calendars as calendars
import yaml
from dotenv import load_dotenv

from .opportunity_brief import render_html, render_markdown, render_text
from .opportunity_data import discover_universe, fetch_snapshot
from .opportunity_delivery import deliver
from .opportunity_engine import evaluate_candidate
from .utils.errors import sanitize_error

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def utc_time(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n")
    temporary.replace(path)


def empty_state():
    return {"version": 2, "snapshots": {}, "alerts": {}, "journal": [], "last_weekly": None, "pending_delivery": None}


def read_state(path):
    if not Path(path).exists():
        return empty_state(), None
    try:
        value = json.loads(Path(path).read_text())
        if (not isinstance(value, dict) or value.get("version") != 2
                or not isinstance(value.get("snapshots"), dict)
                or not isinstance(value.get("alerts"), dict)
                or not isinstance(value.get("journal"), list)
                or any(not isinstance(x, dict) for x in value["snapshots"].values())
                or any(not isinstance(x, dict) for x in value["alerts"].values())
                or any(not isinstance(x, dict) for x in value["journal"])
                or (value.get("pending_delivery") is not None and (
                    not isinstance(value["pending_delivery"], dict)
                    or not isinstance(value["pending_delivery"].get("notification"), dict)
                    or not isinstance(value["pending_delivery"].get("items"), list)))):
            raise ValueError("Unexpected opportunity state format")
        return value, None
    except (OSError, ValueError, TypeError) as error:
        return empty_state(), "Saved comparison state is unreadable. Alerts are withheld for this run: " + sanitize_error(error)


def market_scan_due(now=None):
    """Allow open sessions and a 55-minute closing buffer, including early closes."""
    now = now or datetime.now(UTC)
    day = now.astimezone(NY).date()
    schedule = calendars.get_calendar("NYSE").schedule(start_date=day, end_date=day)
    if schedule.empty:
        return False
    session = schedule.iloc[0]
    return bool(session["market_open"] <= now <= session["market_close"] + timedelta(minutes=55))


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def select_quotes(quotes, watchlist, state, config):
    """Screen every fresh quote; rotate deeper checks rather than a fixed watchlist."""
    minimum = float(config.get("prefilter_drawdown_pct", 15))
    by_symbol = {q["symbol"]: q for q in quotes if isinstance(q, dict) and isinstance(q.get("symbol"), str)}
    eligible = []
    for ticker, quote in by_symbol.items():
        price, high = quote.get("regularMarketPrice"), quote.get("fiftyTwoWeekHigh")
        discount = 100 * (1 - price / high) if _finite(price) and _finite(high) and price > 0 and high > 0 else None
        if ticker in watchlist or ticker in state.get("alerts", {}) or (discount is not None and discount >= minimum):
            eligible.append(quote)
    for ticker in set(watchlist) | set(state.get("alerts", {})):
        if ticker not in by_symbol:
            eligible.append({"symbol": ticker})
    seen = state.get("alerts", {})
    snapshots = state.get("snapshots", {})
    # Reserve capacity for discovery even if the tracked idea list grows.
    eligible.sort(key=lambda quote: (
        snapshots.get(quote["symbol"], {}).get("last_evaluated_at", ""),
        -(quote.get("marketCap") or 0), quote["symbol"],
    ))
    maximum = max(1, min(150, int(config.get("max_deep_checks", 60))))
    tracked = [q for q in eligible if q["symbol"] in seen][:max(1, maximum // 3)]
    tracked_symbols = {q["symbol"] for q in tracked}
    discovery = [q for q in eligible if q["symbol"] not in tracked_symbols]
    # Other tracked names cannot crowd all never-examined names out of discovery.
    discovery.sort(key=lambda q: (q["symbol"] in seen, snapshots.get(q["symbol"], {}).get("last_evaluated_at", ""), -(q.get("marketCap") or 0)))
    return tracked + discovery[:maximum - len(tracked)], len(eligible)


def notification_plan(report, state, config, now):
    """Only delivery success advances this ledger; failed emails remain retryable."""
    week = now.astimezone(NY).strftime("%G-W%V")
    candidates = [x for x in report["items"] if x["status"] == "candidate"]
    if report["status"] == "degraded" or report.get("state_warning"):
        return {"required": False, "kind": "preview", "tickers": [], "reason": "Scan needs attention", "week": week}
    if report["mode"] == "weekly" and state.get("last_weekly") != week:
        return {"required": True, "kind": "weekly", "tickers": [x["ticker"] for x in candidates[:3]], "week": week}
    alerts = state.get("alerts", {})
    fresh, thesis = [], []
    for item in report["items"]:
        last = alerts.get(item["ticker"])
        if item["status"] == "candidate":
            if not last:
                fresh.append(item["ticker"])
                continue
            last_time = utc_time(last.get("at"))
            elapsed = (now - last_time).total_seconds() / 3600 if last_time else 0
            last_price = last.get("price")
            price_change = 100 * (1 - item["price"] / last_price) if _finite(last_price) and last_price > 0 else 0
            if elapsed >= config.get("alert_cooldown_hours", 48) and (
                price_change >= config.get("realert_further_drop_pct", 8) or last.get("status") != "candidate"
            ):
                fresh.append(item["ticker"])
        elif item["status"] == "rejected" and last and last.get("status") == "candidate":
            thesis.append(item["ticker"])
    tickers = thesis[:3] if thesis else fresh[:3]
    return {"required": bool(tickers), "kind": "thesis_change" if thesis else "opportunity",
            "tickers": tickers, "week": week,
            "previous_alerts": {ticker: alerts.get(ticker, {}).get("at") for ticker in tickers}}


def record_delivery(state, report, now):
    notification = report["notification"]
    if notification["kind"] == "weekly":
        state["last_weekly"] = notification["week"]
    by_ticker = {x["ticker"]: x for x in report["items"]}
    for ticker in notification["tickers"]:
        item = by_ticker[ticker]
        state["alerts"][ticker] = {"at": now.isoformat(), "price": item.get("price"), "status": item["status"]}


def run_scan(config, state, watchlist, mode="scan", now=None, universe=None, snapshot_fetcher=None):
    now = now or datetime.now(UTC)
    snapshot_fetcher = snapshot_fetcher or fetch_snapshot
    if universe is None:
        universe = discover_universe(config)
    quotes = universe.get("quotes") or []
    selected, eligible_count = select_quotes(quotes, watchlist, state, config)
    issues = list(universe.get("errors") or [])
    if not universe.get("complete", False):
        issues.append("Discovery coverage is partial; unreturned companies were not screened.")
    items = []
    fetch_failures = 0
    with ThreadPoolExecutor(max_workers=max(1, min(4, int(config.get("workers", 3))))) as pool:
        futures = {
            pool.submit(snapshot_fetcher, q["symbol"], quote=q,
                        cached=state["snapshots"].get(q["symbol"]), config=config, now=now): q["symbol"]
            for q in selected
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                snapshot = future.result()
                item = evaluate_candidate(snapshot, config=config, now=now)
                snapshot["last_evaluated_at"] = now.isoformat()
                # Errors must not evict a usable financial snapshot from the cache.
                if snapshot.get("annual_cashflows") and snapshot.get("quarterly_cashflows"):
                    state["snapshots"][ticker] = snapshot
                else:
                    previous = state["snapshots"].get(ticker, {})
                    previous["last_evaluated_at"] = now.isoformat()
                    state["snapshots"][ticker] = previous
                if (not snapshot.get("info") or not snapshot.get("quote_as_of")
                        or any(check['name'] == 'Fresh quote' and check['passed'] is not True for check in item.get('quality_checks', []))):
                    fetch_failures += 1
                items.append(item)
            except Exception as error:
                fetch_failures += 1
                issues.append(f"{ticker}: " + sanitize_error(error))
    order = {"candidate": 0, "watch": 1, "rejected": 2, "incomplete": 3}
    items.sort(key=lambda x: (order.get(x["status"], 4), -float(x.get("rank") or 0), x["ticker"]))
    # Retain source snapshots for a bounded research universe, not forever.
    if len(state["snapshots"]) > 1500:
        state["snapshots"] = dict(sorted(state["snapshots"].items(), key=lambda x: x[1].get("last_evaluated_at", ""), reverse=True)[:1500])
    severe = not quotes or (selected and fetch_failures / len(selected) > .25)
    if fetch_failures:
        issues.append(f"{fetch_failures} detailed checks lacked a usable current quote.")
    incomplete_count = sum(x['status'] == 'incomplete' for x in items)
    if incomplete_count:
        issues.append(f"{incomplete_count} companies could not be fully assessed because required evidence was missing or stale; they cannot trigger opportunity alerts.")
    severe = severe or bool(items and incomplete_count == len(items))
    status = "degraded" if severe else "partial" if issues else "ok"
    counts = {"screened": len(quotes), "prefilter_passed": eligible_count, "evaluated": len(items),
              "candidates": sum(x["status"] == "candidate" for x in items),
              "watching": sum(x["status"] == "watch" for x in items),
              "rejected": sum(x["status"] == "rejected" for x in items),
              "incomplete": sum(x["status"] == "incomplete" for x in items)}
    source = universe.get("source", "Yahoo Finance dynamic equity screen")
    if universe.get("scope"):
        source += "; " + universe["scope"]
    report = {"version": 2, "generated_at": now.isoformat(), "mode": mode, "status": status,
              "counts": counts, "items": items, "scan_issues": issues,
              "universe": {**{key: value for key, value in universe.items() if key != "quotes"}, "source": source},
              "run_url": os.getenv("GITHUB_SERVER_URL", "https://github.com") + "/" + os.getenv("GITHUB_REPOSITORY", "Kerollosl/stock-check-bot") + "/actions/runs/" + os.getenv("GITHUB_RUN_ID", "")}
    if not os.getenv("GITHUB_RUN_ID"):
        report["run_url"] = "https://github.com/Kerollosl/stock-check-bot/actions/workflows/stock-check.yml"
    day = now.astimezone(NY).date().isoformat()
    earlier_today = next((row.get("tickers", []) for row in state["journal"] if row.get("date") == day), [])
    state["journal"] = [row for row in state["journal"] if row.get("date", "") >= (now.date() - timedelta(days=90)).isoformat() and row.get("date") != day]
    state["journal"].append({"date": day, **counts, "tickers": sorted(set(earlier_today) | {x["ticker"] for x in items if x["status"] == "candidate"})})
    weekly_rows = [row for row in state["journal"] if row["date"] >= (now.astimezone(NY).date() - timedelta(days=6)).isoformat()]
    report["week_activity"] = {"days_scanned": len(weekly_rows), "tickers_surfaced": sorted({ticker for row in weekly_rows for ticker in row.get("tickers", [])})}
    state["last_scan_at"] = now.isoformat()
    report["notification"] = notification_plan(report, state, config, now)
    return report


@click.command()
@click.option("--config", "config_path", default="config.yaml", type=click.Path(exists=True))
@click.option("--state", "state_path", default="state/opportunities.json")
@click.option("--output-dir", default="reports/opportunities")
@click.option("--mode", type=click.Choice(["scan", "weekly"]), default="scan")
@click.option("--scheduled", is_flag=True, help="Skip scans when the NYSE session is closed")
@click.option("--send", is_flag=True, help="Deliver qualifying alerts and advance the notification ledger")
@click.option("--ticker", "tickers", multiple=True, help="Inspect explicit tickers instead of broad discovery")
@click.option("--limit", type=click.IntRange(1, 150), help="Override the per-run detailed research budget")
def cli(config_path, state_path, output_dir, mode, scheduled, send, tickers, limit):
    load_dotenv()
    now = datetime.now(UTC)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    raw_config = yaml.safe_load(Path(config_path).read_text())
    config = raw_config.get("opportunity", {})
    state, warning = read_state(state_path)
    pending = state.get("pending_delivery") if send and not warning else None
    if scheduled and mode == "scan" and not pending and not market_scan_due(now):
        atomic_json(directory / "delivery.json", {"status": "skipped_market_closed"})
        (directory / "summary.md").write_text("Market closed. The opportunity scanner resumes during the next NYSE session.\n")
        click.echo("Market closed; no scan or notification needed.")
        return
    if limit:
        config["max_deep_checks"] = limit
    watch_path = Path(config_path).parent / "watchlist.txt"
    watchlist = [line.strip().upper() for line in watch_path.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")] if watch_path.exists() else raw_config.get("watchlist", [])
    universe = None
    if tickers:
        watchlist = list(dict.fromkeys(x.upper() for x in tickers))
        universe = {"quotes": [{"symbol": x} for x in watchlist], "complete": True,
                    "source": "Explicit ticker inspection", "total_available": len(watchlist), "errors": []}
    if pending:
        click.echo("Retrying an undelivered brief with its original data timestamps…")
        report = pending
    else:
        click.echo("Discovering the universe and checking business quality and valuation…")
        report = run_scan(config, state, watchlist, mode, now, universe=universe)
    if warning:
        report["state_warning"] = warning
        report["scan_issues"].append(warning)
        report["status"] = "degraded"
        report["notification"] = notification_plan(report, state, config, now)
    atomic_json(directory / "report.json", report)
    (directory / "brief.html").write_text(render_html(report))
    (directory / "brief.txt").write_text(render_text(report))
    (directory / "summary.md").write_text(render_markdown(report))
    delivery = {"status": "preview_only" if not send else "not_needed"}
    exit_code = 2 if report["status"] == "degraded" else 0
    if send and report["notification"]["required"]:
        try:
            delivery = deliver(report, os.getenv("GITHUB_REPOSITORY"), config.get("github_fallback", True))
            record_delivery(state, report, now)
            state["pending_delivery"] = None
        except Exception as error:
            # Error type is enough for auth failures; never publish server replies or credentials.
            delivery = {"status": "failed", "error": type(error).__name__ + ": delivery was not confirmed. Check the SMTP repository secrets and sender settings."}
            state["pending_delivery"] = report
            exit_code = 3
    atomic_json(directory / "delivery.json", delivery)
    # A dry run can warm the data cache, but never marks an alert or weekly brief delivered.
    if not warning:
        atomic_json(state_path, state)
    click.echo(json.dumps({"counts": report["counts"], "scan_status": report["status"], "notification": report["notification"], "delivery": delivery}, indent=2))
    if exit_code:
        raise click.exceptions.Exit(exit_code)


if __name__ == "__main__":
    cli()
