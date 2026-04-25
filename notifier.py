"""
SMS Notifier via Twilio
Sends text alerts for trade signals and morning briefs.
"""

import os
import logging
from twilio.rest import Client

log = logging.getLogger(__name__)

TWILIO_SID    = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM   = os.environ["TWILIO_FROM_NUMBER"]   # e.g. +12035550001
ALERT_TO      = os.environ["ALERT_PHONE_NUMBER"]   # your cell e.g. +12035550002


def send_text(message: str):
    """Send an SMS via Twilio. Truncates at 1600 chars (Twilio limit)."""
    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        msg = client.messages.create(
            body=message[:1600],
            from_=TWILIO_FROM,
            to=ALERT_TO
        )
        log.info(f"SMS sent: {msg.sid}")
    except Exception as e:
        log.error(f"SMS send failed: {e}")
