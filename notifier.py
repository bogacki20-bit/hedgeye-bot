"""
Pushover Notifier
Sends instant push notifications for trade signals and morning briefs.
"""

import os
import logging
import requests

log = logging.getLogger(__name__)

PUSHOVER_TOKEN   = os.environ["PUSHOVER_TOKEN"]
PUSHOVER_USER    = os.environ["PUSHOVER_USER"]
PUSHOVER_URL     = "https://api.pushover.net/1/messages.json"


def send_text(message: str, title: str = "Hedgeye Bot", priority: int = 0):
    try:
        response = requests.post(PUSHOVER_URL, data={
            "token":   PUSHOVER_TOKEN,
            "user":    PUSHOVER_USER,
            "title":   title,
            "message": message[:1024],
            "priority": priority,
            "sound":   "cashregister"
        })
        if response.status_code == 200:
            log.info("Pushover notification sent successfully")
        else:
            log.error(f"Pushover error: {response.status_code} {response.text}")
    except Exception as e:
        log.error(f"Pushover send failed: {e}")


def send_signal_alert(ticker: str, direction: str, conviction: str,
                      entry: float = None, target: float = None,
                      stop: float = None, summary: str = ""):
    lines = [f"{direction} {ticker} — {conviction}"]
    if entry:
        lines.append(f"Entry: ${entry:.2f}")
    if target:
        lines.append(f"Target: ${target:.2f}")
    if stop:
        lines.append(f"Stop: ${stop:.2f}")
    if entry and target and stop:
        risk = entry - stop
        reward = target - entry
        if risk > 0:
            lines.append(f"R/R: {round(reward/risk, 1)}:1")
    if summary:
        lines.append(f"\n{summary[:200]}")
    send_text("\n".join(lines), title=f"🚨 Hedgeye Signal — {ticker}", priority=1)


def send_trim_alert(ticker: str, pnl_pct: float, suggested_trim: str, redeploy_into: str = ""):
    msg = f"{ticker} up {pnl_pct:.1f}%\nSuggested: {suggested_trim}"
    if redeploy_into:
        msg += f"\nRedeploy into: {redeploy_into}"
    msg += "\n\nReply TRIM or HOLD"
    send_text(msg, title=f"✂️ Trim Alert — {ticker}", priority=1)


def send_buffer_alert(available: float, threshold: float, upcoming_outflows: float = 0):
    msg = f"Available: ${available:,.0f}\nThreshold: ${threshold:,.0f}\n"
    if upcoming_outflows:
        msg += f"Upcoming outflows: ${upcoming_outflows:,.0f}\n"
    msg += "\nConsider trimming a position."
    send_text(msg, title="⚠️ Cash Buffer Warning", priority=1)

