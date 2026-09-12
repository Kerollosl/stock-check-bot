"""Concise opportunity alerts shared by email, previews, and GitHub fallback."""

from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape


def number(value, digits=1, suffix="", signed=False):
    if not isinstance(value, (int, float)):
        return "Unavailable"
    return f"{value:+,.{digits}f}{suffix}" if signed else f"{value:,.{digits}f}{suffix}"


def money(value):
    return f"${value:,.2f}" if isinstance(value, (int, float)) else "Unavailable"


def safe_url(value):
    try:
        return value if urlparse(str(value)).scheme in ("https", "http") else ""
    except ValueError:
        return ""


def short_quality(item):
    metrics = item.get("metrics") or {}
    facts = []
    if isinstance(metrics.get("operating_margin_pct"), (int, float)):
        facts.append(f"{number(metrics['operating_margin_pct'], 0, '%')} operating margin")
    if isinstance(metrics.get("revenue_growth_pct"), (int, float)):
        facts.append(f"{number(metrics['revenue_growth_pct'], 0, '%', signed=True)} revenue growth")
    annual = metrics.get("annual_owner_cash_flows") or []
    if annual and all(isinstance(value, (int, float)) and value > 0 for value in annual):
        facts.append(f"positive cash flow for {len(annual)} years")
    return "; ".join(facts) + "." if facts else "Passed every business-quality check."


def short_risk(item):
    risk = str((item.get("risks") or ["The valuation assumptions may be wrong."])[0])
    short = risk.split(";", 1)[0].strip()
    if len(short) > 140:
        short = short[:137].rsplit(" ", 1)[0] + "…"
    return short if short.endswith((".", "!", "?", "…")) else short + "."


def wait_reason(item):
    price = item.get("price")
    research_price = (item.get("valuation") or {}).get("research_price")
    if (isinstance(price, (int, float)) and isinstance(research_price, (int, float))
            and price > research_price > 0):
        gap = 100 * (price / research_price - 1)
        return f"Price is {number(gap, 0, '%')} above the research level."
    return "The conservative valuation test did not clear."


def as_of(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            ZoneInfo("America/New_York")
        )
        return moment.strftime("%b %-d, %-I:%M %p ET")
    except (TypeError, ValueError):
        return "Unavailable"


def _display(report):
    tickers = report.get("notification", {}).get("tickers", [])
    by_ticker = {item["ticker"]: item for item in report.get("items", [])}
    return [by_ticker[ticker] for ticker in tickers if ticker in by_ticker]


def brief_context(report):
    candidates = [item for item in report.get("items", []) if item["status"] == "candidate"]
    watching = [item for item in report.get("items", []) if item["status"] == "watch"]
    kind = report.get("notification", {}).get("kind", "preview")
    selected = _display(report)
    if kind in ("weekly", "preview") or not report.get("notification", {}).get("required"):
        selected = candidates[:3]

    if kind == "thesis_change" and selected:
        title = "Step back: " + ", ".join(item["ticker"] for item in selected)
        subject = "Stock Check: " + title
        intro = "A previously surfaced company failed a business-quality check."
    elif selected:
        title = "Research now: " + ", ".join(item["ticker"] for item in selected)
        subject = "Stock Check: " + title
        intro = "The price now clears every quality and valuation rule."
    else:
        title = "No action."
        subject = "Stock Check: no action this week"
        intro = "None of the elite large-cap companies checked is cheap enough."

    status = report.get("status")
    if status == "degraded":
        selected = []
        title = "No signal."
        subject = "Stock Check: scan incomplete"
        intro = "The data was incomplete, so the scanner withheld a conclusion."
    elif not selected:
        intro = "No qualifying signal from the completed checks."

    generated = datetime.fromisoformat(report["generated_at"]).astimezone(
        ZoneInfo("America/New_York")
    )
    limit = report.get("universe", {}).get("requested_limit") or report.get("counts", {}).get("screened", 0)
    incomplete = report.get("counts", {}).get("incomplete", 0)
    return {
        "report": report,
        "title": title,
        "subject": subject,
        "intro": intro,
        "selected": selected,
        "watching": watching[:1] if not selected and status != "degraded" else [],
        "date_label": generated.strftime("%B %-d, %Y"),
        "universe_label": f"largest {limit} eligible U.S.-listed companies",
        "coverage_note": f"{incomplete} companies lacked complete data." if incomplete else "",
        "kind": kind,
        "money": money,
        "number": number,
        "safe_url": safe_url,
        "as_of": as_of,
        "short_quality": short_quality,
        "short_risk": short_risk,
        "wait_reason": wait_reason,
    }


def render_html(report):
    environment = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    return environment.get_template("opportunity_email.html").render(**brief_context(report))


def _item_lines(item):
    valuation = item.get("valuation") or {}
    if item["status"] == "candidate":
        return [
            f"{item['ticker']} — {item['name']} — {money(item.get('price'))}",
            f"Research zone: {money(valuation.get('research_price', valuation.get('entry_price')))} or below.",
            "Why: " + short_quality(item),
            "Risk: " + short_risk(item),
            "Next: Read the latest filing and earnings call.",
        ]
    return [
        f"{item['ticker']} — {item['name']}",
        "Step back: " + str(item.get("first_rejection") or "A business-quality check failed."),
        "Next: " + item.get("next_step", "Review the latest company filing."),
    ]


def render_text(report):
    ctx = brief_context(report)
    lines = [ctx["title"], ctx["date_label"], "", ctx["intro"]]
    for item in ctx["selected"]:
        lines += ["", *_item_lines(item)]
    if ctx["watching"]:
        item = ctx["watching"][0]
        valuation = item.get("valuation") or {}
        lines += [
            "",
            f"Closest: {item['ticker']} — {item['name']} — {money(item.get('price'))}",
            f"Wait. Recheck at {money(valuation.get('research_price', valuation.get('entry_price')))} or below.",
            "Reason: " + wait_reason(item),
        ]
    if safe_url(report.get("run_url")):
        lines += ["", "Details: " + report["run_url"]]
    if ctx["coverage_note"]:
        lines += ["", ctx["coverage_note"]]
    lines += ["", "Research screen, not an instruction to buy or sell."]
    return "\n".join(lines)


def render_markdown(report):
    """Keep the GitHub fallback as terse as the direct email."""
    ctx = brief_context(report)
    lines = [f"# {ctx['title']}", "", ctx["intro"]]
    for item in ctx["selected"]:
        lines += ["", f"## {item['ticker']} · {item['name']}", ""]
        lines += [f"- {line}" for line in _item_lines(item)[1:]]
    if ctx["watching"]:
        item = ctx["watching"][0]
        valuation = item.get("valuation") or {}
        lines += [
            "",
            f"**Closest:** {item['ticker']} · {item['name']} · {money(item.get('price'))}",
            "",
            f"Wait. Recheck at {money(valuation.get('research_price', valuation.get('entry_price')))} or below.",
            "",
            "Reason: " + wait_reason(item),
        ]
    if safe_url(report.get("run_url")):
        lines += ["", f"[Details and sources]({report['run_url']})"]
    if ctx["coverage_note"]:
        lines += ["", ctx["coverage_note"]]
    lines += ["", "_Research screen, not an instruction to buy or sell._"]
    return "\n".join(lines)
