"""Readable opportunity briefs, shared by email, previews and GitHub fallback."""

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


def as_of(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York'))
        return moment.strftime('%b %-d, %-I:%M %p ET')
    except (TypeError, ValueError):
        return 'Unavailable'


def _display(report):
    selected = report.get("notification", {}).get("tickers", [])
    by_ticker = {item["ticker"]: item for item in report.get("items", [])}
    return [by_ticker[ticker] for ticker in selected if ticker in by_ticker]


def brief_context(report):
    candidates = [x for x in report.get("items", []) if x["status"] == "candidate"]
    watching = [x for x in report.get("items", []) if x["status"] == "watch"]
    business_checks = {'Profitable business', 'Operating profitability', 'Revenue resilience', 'Balance-sheet capacity', 'Durable owner cash flow', 'Cash-flow resilience'}
    rejected = [x for x in report.get("items", []) if x["status"] == "rejected" and any(check.get('name') in business_checks and check.get('passed') is False for check in x.get('quality_checks', []))]
    close_to_zone = [x for x in watching if (x.get("valuation") or {}).get("research_price", 0) >= (x.get("price") or 0) * .8]
    selected = _display(report)
    kind = report.get("notification", {}).get("kind", "preview")
    if kind in ("weekly", "preview") or not report.get("notification", {}).get("required"):
        selected = candidates[:3]
    if kind == "opportunity" and selected:
        lead = selected[0]
        title = f"{lead['name']}, at a price worth researching."
        subject = f"{lead['ticker']}: {number(lead.get('metrics', {}).get('margin_of_safety_pct'), 0, '%')} below model value — worth a closer look"
        intro = "The business cleared the quality checks and its price entered your research zone. Here is the case to investigate, and what could undo it."
    elif kind == "thesis_change":
        title = "An earlier idea needs another look."
        subject = "Thesis check: " + ", ".join(x["ticker"] for x in selected)
        intro = "A previously surfaced company no longer clears the same quality or valuation checks. Review the changed evidence before relying on the earlier brief."
    elif candidates:
        title = "Good businesses. Prices worth a closer look."
        subject = f"Your weekly shortlist: {len(candidates)} research candidates"
        intro = "Your long-term shortlist, refreshed from current prices and reported cash flows. Start with the case, then test the assumptions."
    else:
        title = "Patience is part of the strategy."
        subject = "Your weekly shortlist: no qualifying bargains yet"
        intro = "Nothing checked in this run cleared every quality and valuation rule. Here are the closest business-and-price combinations, and why other apparent discounts did not make the cut."
    if report.get("status") == "degraded":
        title = "The scanner needs attention."
        subject = "Stock Check: market scan incomplete"
        intro = "The scan could not establish enough current evidence. This is a coverage update; it does not mean there are no opportunities."
    generated = datetime.fromisoformat(report["generated_at"]).astimezone(ZoneInfo("America/New_York"))
    return dict(report=report, title=title, subject=subject, intro=intro,
                selected=selected, watching=(close_to_zone or watching)[:3], rejected=rejected[:2],
                watch_title="Getting close" if close_to_zone else "Good businesses. Still too expensive.",
                watch_intro="These cleared the business checks and are within another 20% price decline of the research zone." if close_to_zone else "These cleared the business checks, but even the closest need a substantial price decline under this conservative model.",
                candidates=candidates, date_label=generated.strftime("%B %-d, %Y · %-I:%M %p ET"),
                kind=kind, money=money, number=number, safe_url=safe_url, as_of=as_of)


def render_html(report):
    environment = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    return environment.get_template("opportunity_email.html").render(**brief_context(report))


def render_text(report):
    ctx = brief_context(report)
    counts = report.get("counts", {})
    lines = [ctx["title"], ctx["date_label"], "", ctx["intro"], "",
             f"{counts.get('screened', 0)} stocks screened · {counts.get('evaluated', 0)} examined · {counts.get('candidates', 0)} research candidates", ""]
    for item in ctx["selected"]:
        metrics, valuation = item.get("metrics", {}), item.get("valuation") or {}
        lines += [f"{item['ticker']} — {item['name']}", f"Price: {money(item.get('price'))}",
                  item.get("why_now", ""),
                  ("Changed evidence: " + str(item.get("first_rejection", ""))) if item['status'] != 'candidate' else ("Why it passed: " + item.get("quality_summary", "")),
                  "Valuation: " + item.get("valuation_summary", ""),
                  f"Model scenarios: stress {money(valuation.get('low'))} / base {money(valuation.get('base'))} / upside {money(valuation.get('high'))}",
                  f"Research below: {money(valuation.get('research_price', valuation.get('entry_price')))}" if item['status'] == 'candidate' else "Price alone cannot restore this idea; the failed business check must be resolved.",
                  "What could go wrong: " + "; ".join(item.get("risks", [])[:3]),
                  "Next step: " + item.get("next_step", "Read the latest filing."), ""]
        for source in item.get("sources", []):
            if safe_url(source.get("url")):
                lines.append(f"{source['label']}: {source['url']}")
        lines.append("")
    if ctx["watching"]:
        lines.append(ctx["watch_title"].upper())
        for item in ctx["watching"]:
            valuation = item.get("valuation") or {}
            lines.append(f"{item['ticker']}: {money(item.get('price'))}; research price {money(valuation.get('research_price', valuation.get('entry_price')))}. {item.get('first_rejection', '')}")
        lines.append("")
    if ctx["rejected"]:
        lines.append("CHEAP-LOOKING, BUT DID NOT PASS")
        lines += [f"{x['ticker']}: {x.get('first_rejection', '')}" for x in ctx["rejected"]]
        lines.append("")
    lines += ["Coverage: " + report.get("universe", {}).get("source", "Unavailable")]
    lines += ["Scan note: " + note for note in report.get("scan_issues", [])[:4]]
    lines += ["", "Screening candidates for further research, not verified bargains. Model values depend on cash-flow assumptions; a fall from a past high is not a valuation discount. No trades are placed."]
    if safe_url(report.get("run_url")):
        lines += ["Full scan and source snapshots: " + report["run_url"]]
    return "\n".join(lines)


def render_markdown(report):
    """Keep a useful fallback even without an authenticated email sender."""
    ctx = brief_context(report)
    lines = [f"# {ctx['title']}", "", ctx["intro"], "",
             f"**{report.get('counts', {}).get('screened', 0)} stocks screened** · {ctx['date_label']}", ""]
    for item in ctx["selected"]:
        value = item.get("valuation") or {}
        lines += [f"## {item['ticker']} · {item['name']}", "", item.get("why_now", ""), "",
                  (f"**Why the business passed:** {item.get('quality_summary', '')}" if item['status'] == 'candidate' else f"**What changed:** {item.get('first_rejection', '')}"), "",
                  f"**Price {money(item.get('price'))}** · Model range {money(value.get('low'))}–{money(value.get('high'))}", "",
                  (f"Research below {money(value.get('research_price', value.get('entry_price')))}" if item['status'] == 'candidate' else "Price alone cannot restore this idea; resolve the failed business check first."), "",
                  "**The valuation case:** " + item.get("valuation_summary", ""), "",
                  "**What could go wrong:** " + "; ".join(item.get("risks", [])[:3]), "",
                  "**Next step:** " + item.get("next_step", "Read the latest filing."), ""]
        links = [f"[{x['label']}]({x['url']})" for x in item.get("sources", []) if safe_url(x.get("url"))]
        lines += [" · ".join(links), ""]
    if ctx["watching"]:
        lines += ["## " + ctx["watch_title"], "", ctx["watch_intro"], ""]
        lines += [f"- **{x['ticker']}** — {x.get('first_rejection', '')}" for x in ctx["watching"]]
    if ctx["rejected"]:
        lines += ["", "## Why these apparent bargains did not pass", ""]
        lines += [f"- **{x['ticker']}** — {x.get('first_rejection', '')}" for x in ctx["rejected"]]
    lines += ["", "**Coverage:** " + report.get("universe", {}).get("source", "Unavailable")]
    lines += ["- " + note for note in report.get("scan_issues", [])[:4]]
    if safe_url(report.get("run_url")):
        lines += ["", f"[Open the full report and email preview]({report['run_url']})"]
    lines += ["", "_Research candidates, not verified bargains. Valuation ranges are assumption-driven estimates. No trades are placed._"]
    return "\n".join(lines)
