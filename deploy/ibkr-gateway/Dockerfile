# Dockerfile for the ibkr-gateway Railway service.
# Wraps voyz/ibeam (a managed Interactive Brokers Client Portal / Gateway
# container with auto-login + 2FA handling). Image documentation:
#   https://github.com/Voyz/ibeam
#
# Required env vars on the Railway service (set in dashboard, never commit):
#   IBEAM_ACCOUNT             — IB username
#   IBEAM_PASSWORD            — IB password
#   IBEAM_KEY                 — encryption key for the above (random string)
#
# 2FA — pick ONE of:
#   IBEAM_TWO_FA_HANDLER=NONE              (paper account: no 2FA required)
#   IBEAM_TOTP_SECRET=<base32>             (TOTP shared secret)
#   IBEAM_TWO_FA_HANDLER=GMAIL + GMAIL_*   (Gmail-based; ibeam reads codes)
#
# The container exposes the Client Portal Web API on port 5000 inside the
# Railway private network at host `ibkr-gateway.railway.internal`. Do NOT
# attach a public domain — the bot reaches it via internal DNS only.

# Pinned to 0.5.11 (2026-04-09) as a regression test against 0.5.12
# (2026-04-21, the current :latest) which appears to have post-submit
# selectors that don't match the live IBKR login UI. If 0.5.11 also
# fails identically, the IBKR UI is incompatible with both releases and
# we pivot to Polygon until ibeam ships an upstream fix.
FROM voyz/ibeam:0.5.11

# ibeam health checks against itself at /v1/api/iserver/auth/status. No
# additional layers needed; the upstream image is a complete runtime.
EXPOSE 5000
