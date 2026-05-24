# Custom IBKR Client Portal API gateway.
#
# Replaces voyz/ibeam (see legacy/Dockerfile.ibeam for the old image).
# We control the Selenium login flow + selectors directly because both
# ibeam 0.5.11 and 0.5.12 ship post-login selectors that don't match the
# current IBKR UI (#twofactbase, #xyz-field-bronze-response, etc.).
#
# Image layout:
#   - debian-slim base
#   - chromium + chromedriver (apt, matched versions — no driver download
#     at runtime, no Chrome-major-version skew)
#   - python 3.11
#   - app code in /app
#   - gunicorn serves Flask on :5000 with a SINGLE worker (one selenium
#     session, one cookie jar)
#
# Required env vars on the Railway service (set in dashboard, never commit):
#   IBEAM_ACCOUNT       — IB username
#   IBEAM_PASSWORD      — IB password (plaintext; operator decided no Fernet)
#   IBEAM_TOTP_SECRET   — base32 TOTP secret (omit for paper accounts w/o 2FA)
#
# Optional:
#   IBKR_LOGIN_URL      — override login entry point
#   IBKR_API_BASE       — override REST proxy target
#   IBKR_TICKLE_INTERVAL / IBKR_REAUTH_INTERVAL / IBKR_LOGIN_RETRY_DELAY
#
# Internal-only on the Railway private network at
# http://ibkr-gateway.railway.internal:5000. Do NOT attach a public domain.

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    # chromedriver location used by auth.py — keep in sync with the apt pkg
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    CHROME_BINARY=/usr/bin/chromium

# Chromium + chromedriver via apt guarantees matched major versions. The
# other packages are runtime deps for headless chrome (fonts, libnss3, etc.)
# that the chromium package doesn't pull in transitively on slim.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libnss3 \
        libxss1 \
        libxtst6 \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first — cache layer survives across code edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code last so iteration on app.py / auth.py / session.py doesn't bust
# the pip layer.
COPY app.py auth.py session.py ./

EXPOSE 5000

# tini is PID 1 so SIGTERM from Railway reaps gunicorn + the selenium
# subprocesses cleanly. --workers 1 is REQUIRED — see the note in app.py
# about the single shared cookie jar.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
