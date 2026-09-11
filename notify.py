"""
Discord webhook notifications.

Sends a formatted summary after every real cycle and an immediate alert
whenever a cycle errors or is skipped.  All functions are fire-and-forget:
they catch every exception and log a warning — the bot never crashes because
Discord is unreachable or the URL is misconfigured.

Configuration: set DISCORD_WEBHOOK_URL in .env.
If the variable is absent, all functions silently no-op.
"""
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import timezone, datetime

log = logging.getLogger(__name__)

_DISCORD_LIMIT = 2000  # Discord max content length


def _get_webhook() -> str | None:
    return os.environ.get("DISCORD_WEBHOOK_URL") or None


def _post(webhook: str, content: str) -> None:
    payload = json.dumps({"content": content[:_DISCORD_LIMIT]}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "trading-bot/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        status = resp.status
    if status not in (200, 204):
        log.warning("Discord webhook returned unexpected status %s.", status)


def send_alert(title: str, body: str = "") -> None:
    """Immediate alert — call on cycle errors and skipped cycles."""
    webhook = _get_webhook()
    if not webhook:
        return
    try:
        text = f"**ALERT: {title}**"
        if body:
            text += f"\n{body}"
        _post(webhook, text)
        log.debug("Discord alert sent: %s", title)
    except Exception as exc:
        log.warning("Discord alert failed (bot continuing): %s", exc)


def send_summary(record: dict) -> None:
    """Formatted cycle summary — call after every completed real cycle."""
    webhook = _get_webhook()
    if not webhook:
        return
    try:
        _post(webhook, _format_summary(record))
        log.debug("Discord summary sent.")
    except Exception as exc:
        log.warning("Discord summary failed (bot continuing): %s", exc)


def _format_summary(record: dict) -> str:
    ts = record.get("timestamp", "")[:16].replace("T", " ")
    dry_tag  = " [DRY-RUN]" if record.get("dry_run") else ""
    test_tag = " [TEST]"    if record.get("test_run") else ""
    header = f"**CYCLE SUMMARY**{dry_tag}{test_tag}  {ts} UTC"

    # Account / P&L line
    acct    = record.get("account_before", {})
    equity  = acct.get("equity", 0.0)
    last_eq = acct.get("last_equity") or equity
    day_pl  = equity - last_eq
    day_pct = (day_pl / last_eq * 100) if last_eq else 0.0

    bm     = record.get("benchmark") or {}
    vs_spy = f"{bm.get('vs_benchmark_pct', 0):+.2f}%" if bm else "n/a"

    lines = [
        header,
        f"Equity: ${equity:,.2f} | Day P/L: ${day_pl:+,.2f} ({day_pct:+.2f}%) | vs SPY: {vs_spy}",
    ]

    # Positions
    positions = acct.get("positions", [])
    if positions:
        pos_strs = [
            f"{p['symbol']} {p['qty']:.0f}sh (P/L ${p['unrealized_pl']:+,.2f})"
            for p in positions
        ]
        lines.append(f"Positions ({len(positions)}): " + ", ".join(pos_strs))
    else:
        lines.append("Positions: none")

    # Approved trades
    approved = record.get("approved_decisions", [])
    active = [d for d in approved if d.get("action") != "hold" and d.get("quantity", 0) > 0]
    if active:
        trade_strs = [f"{d['action'].upper()} {d['quantity']}x {d['symbol']}" for d in active]
        lines.append("Trades: " + ", ".join(trade_strs))
    else:
        lines.append("Trades: none")

    # Errors (bold so they stand out)
    errs = record.get("errors", [])
    if errs:
        err_text = " | ".join(errs)
        lines.append(f"**Errors ({len(errs)}):** {err_text}")

    return "\n".join(lines)
