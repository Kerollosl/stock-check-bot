"""Authenticated email delivery, with an explicit GitHub fallback."""

import hashlib
import json
import os
import smtplib
import ssl
import subprocess
import tempfile
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path

from .opportunity_brief import brief_context, render_html, render_markdown, render_text


def _address(value, name):
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"{name} must contain one valid email address")
    address = parseaddr(value)[1]
    if address != value.strip() or address.count("@") != 1 or "," in address:
        raise ValueError(f"{name} must contain one valid email address")
    return address


def email_message(report, sender, recipient):
    message = EmailMessage()
    message["From"] = formataddr(("Stock Check · Opportunity Brief", _address(sender, "SMTP_FROM")))
    message["To"] = _address(recipient, "EMAIL_TO")
    message["Subject"] = brief_context(report)["subject"]
    digest = hashlib.sha256(json.dumps(report.get("notification", {}), sort_keys=True).encode()).hexdigest()[:24]
    message["Message-ID"] = f"<stock-check-{digest}@{sender.split('@')[1]}>"
    message.set_content(render_text(report))
    message.add_alternative(render_html(report), subtype="html")
    return message


def deliver_email(report):
    password = os.getenv("SMTP_PASSWORD", "")
    user = os.getenv("SMTP_USER", "")
    sender = os.getenv("SMTP_FROM", user)
    recipient = os.getenv("EMAIL_TO", "")
    if not all((password, user, sender, recipient)):
        raise RuntimeError("Direct email needs SMTP_USER, SMTP_PASSWORD and EMAIL_TO. Add repository secrets; never put credentials in config.yaml.")
    message = email_message(report, sender, recipient)
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as client:
            client.login(user, password.replace(" ", "") if host == "smtp.gmail.com" else password)
            refused = client.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as client:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
            client.login(user, password)
            refused = client.send_message(message)
    if refused:
        raise RuntimeError("Mail server refused the recipient; the notification remains pending")
    return {"channel": "email", "status": "accepted_by_mail_server"}


def deliver_github(report, repo):
    """Fallback only when no SMTP credential exists; do not hide SMTP failures."""
    if not repo or repo.count("/") != 1:
        raise ValueError("GitHub fallback needs an owner/repository")
    notification = report.get("notification", {})
    event_id = hashlib.sha256(json.dumps(notification, sort_keys=True).encode()).hexdigest()[:24]
    marker = f"<!-- stock-check-event:{event_id} -->"
    listing = subprocess.run(
        ["gh", "issue", "list", "--repo", repo, "--state", "all", "--search", f"{event_id} in:body", "--json", "number,body,url"],
        check=True, capture_output=True, text=True, timeout=45,
    )
    for issue in json.loads(listing.stdout):
        if marker in issue.get("body", ""):
            return {"channel": "github", "status": "already_delivered", "url": issue["url"]}
    body = render_markdown(report) + "\n\n" + marker
    body += "\n\n_Direct HTML email is not connected yet. Add the SMTP repository secrets to receive the designed email._"
    with tempfile.TemporaryDirectory(prefix="stock-check-issue-") as directory:
        path = Path(directory) / "body.md"
        path.write_text(body)
        result = subprocess.run(
            ["gh", "issue", "create", "--repo", repo, "--title", brief_context(report)["subject"],
             "--body-file", str(path), "--assignee", repo.split("/")[0]],
            check=True, capture_output=True, text=True, timeout=45,
        )
    return {"channel": "github", "status": "delivered", "url": result.stdout.strip()}


def deliver(report, repo=None, allow_github_fallback=True):
    if os.getenv("SMTP_PASSWORD"):
        return deliver_email(report)
    if allow_github_fallback and repo:
        return deliver_github(report, repo)
    raise RuntimeError("Direct email is not configured; the notification remains pending")
