"""
Hedgeye Portal Scraper
Logs into app.hedgeye.com using email/password, scrapes the feed every
15 minutes, and passes content to the classifier.
"""

import os
import time
import logging
import hashlib
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from classifier import classify_and_extract
from notifier import send_notification
from database import save_item, get_seen_ids, mark_morning_brief_sent, was_morning_brief_sent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

HEDGEYE_EMAIL      = os.environ["HEDGEYE_EMAIL"]
HEDGEYE_PASSWORD   = os.environ["HEDGEYE_PASSWORD"]
SCRAPE_INTERVAL    = int(os.getenv("SCRAPE_INTERVAL_SECONDS", "900"))
MORNING_BRIEF_HOUR = int(os.getenv("MORNING_BRIEF_HOUR", "7"))

# Disable Chromium's automation-detection flags so reCAPTCHA scores higher
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--disable-extensions",
]

# Override navigator.webdriver before any page script runs
STEALTH_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"


def make_context(browser):
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        timezone_id="America/New_York",
    )
    context.add_init_script(STEALTH_SCRIPT)
    return context


def login(page):
    """Authenticate with Hedgeye. Raises RuntimeError if reCAPTCHA blocks login."""
    log.info("Navigating to Hedgeye login page...")
    page.goto("https://app.hedgeye.com/users/sign_in", wait_until="networkidle")
    time.sleep(2)

    log.info("Filling credentials...")
    page.type('input[name="user[email]"]',    HEDGEYE_EMAIL,    delay=60)
    page.type('input[name="user[password]"]', HEDGEYE_PASSWORD, delay=60)
    time.sleep(1)

    page.click('input[type="submit"], button[type="submit"]')

    try:
        page.wait_for_url("**/feed_items**", timeout=30000)
        log.info("Login successful.")
    except PlaywrightTimeout:
        _handle_login_failure(page)


def _handle_login_failure(page):
    current = page.url
    log.error(f"Login did not redirect to feed_items — stuck on: {current}")

    if page.query_selector("iframe[src*='recaptcha'], .g-recaptcha, [data-sitekey]"):
        msg = (
            "⚠️ Hedgeye Bot: reCAPTCHA challenge appeared during login. "
            "The bot cannot solve it automatically. "
            "Try reducing SCRAPE_INTERVAL_SECONDS or check Railway logs."
        )
        log.error("reCAPTCHA detected on login page.")
        send_notification(msg)
        raise RuntimeError("reCAPTCHA blocked login")

    error_el = page.query_selector(".alert, .flash-error, #error_explanation")
    if error_el:
        log.error(f"Login error message: {error_el.inner_text().strip()}")

    raise RuntimeError(f"Login failed — URL after submit: {current}")


def is_logged_in(page) -> bool:
    return "sign_in" not in page.url and "login" not in page.url


def scrape_feed(page) -> list[dict]:
    log.info("Scraping feed...")
    page.goto("https://app.hedgeye.com/feed_items", wait_until="networkidle")
    time.sleep(2)

    items = []
    cards = page.query_selector_all("article, .feed-item, [data-feed-item], .card")

    for card in cards:
        try:
            title_el  = card.query_selector("h2, h3, h4, .title, .headline")
            body_el   = card.query_selector("p, .summary, .body, .excerpt")
            link_el   = card.query_selector("a[href*='/feed_items/']")
            time_el   = card.query_selector("time, .timestamp, .date")

            title     = title_el.inner_text().strip()  if title_el  else ""
            body      = body_el.inner_text().strip()   if body_el   else ""
            link      = link_el.get_attribute("href")  if link_el   else ""
            timestamp = time_el.get_attribute("datetime") if time_el else datetime.now(timezone.utc).isoformat()

            if not title and not body:
                continue

            if link and not link.startswith("http"):
                link = "https://app.hedgeye.com" + link

            uid = link if link else hashlib.md5((title + body).encode()).hexdigest()

            items.append({
                "id":        uid,
                "title":     title,
                "body":      body,
                "link":      link,
                "timestamp": timestamp,
                "source":    "portal_scrape"
            })
        except Exception as e:
            log.warning(f"Error parsing card: {e}")
            continue

    log.info(f"Found {len(items)} items on feed.")
    return items


def fetch_full_content(page, item: dict) -> dict:
    if not item.get("link"):
        return item

    try:
        page.goto(item["link"], wait_until="networkidle", timeout=20000)
        time.sleep(1)

        selectors = [
            ".research-note-body",
            ".article-body",
            ".content-body",
            "article",
            "main .prose",
            ".feed-item-content"
        ]
        full_text = ""
        for sel in selectors:
            el = page.query_selector(sel)
            if el:
                full_text = el.inner_text().strip()
                if len(full_text) > 200:
                    break

        if full_text:
            item["full_content"] = full_text
        log.info(f"Fetched full content for: {item['title'][:60]}")
    except PlaywrightTimeout:
        log.warning(f"Timeout fetching full content: {item['link']}")
    except Exception as e:
        log.warning(f"Error fetching full content: {e}")

    return item


def should_send_morning_brief() -> bool:
    now = datetime.now()
    return now.hour == MORNING_BRIEF_HOUR and not was_morning_brief_sent(now.date())


def build_morning_brief(new_items: list[dict]) -> str:
    if not new_items:
        return "Hedgeye morning brief: No new signals overnight. Check portal for updates."

    signals  = [i for i in new_items if i.get("classified_type") == "trade_signal"]
    macro    = [i for i in new_items if i.get("classified_type") == "market_situation"]
    research = [i for i in new_items if i.get("classified_type") == "sector_research"]
    other    = [i for i in new_items if i.get("classified_type") not in ("trade_signal", "market_situation", "sector_research")]

    lines = [f"📊 Hedgeye Morning Brief — {datetime.now().strftime('%b %d')}"]

    if signals:
        lines.append(f"\n🟢 SIGNALS ({len(signals)}):")
        for s in signals[:5]:
            lines.append(f"  {s.get('direction','Long')} {s.get('ticker','?')} — {s.get('conviction','')}")

    if macro:
        lines.append(f"\n📈 MACRO ({len(macro)}):")
        for m in macro[:2]:
            lines.append(f"  {m.get('summary', m.get('title',''))[:80]}")

    if research:
        lines.append(f"\n🔬 RESEARCH: {len(research)} new notes")

    if other:
        lines.append(f"\n📋 OTHER: {len(other)} items")

    lines.append("\napp.hedgeye.com")
    return "\n".join(lines)


def run_scrape_cycle(page, seen_ids: set) -> list[dict]:
    raw_items = scrape_feed(page)
    new_items = [i for i in raw_items if i["id"] not in seen_ids]

    if not new_items:
        log.info("No new items found.")
        return []

    log.info(f"{len(new_items)} new items — fetching full content and classifying...")

    processed = []
    for item in new_items:
        item = fetch_full_content(page, item)
        item = classify_and_extract(item)
        save_item(item)
        seen_ids.add(item["id"])
        processed.append(item)
        log.info(f"  [{item.get('classified_type','unknown')}] {item['title'][:60]}")

    return processed


def main():
    log.info("Starting Hedgeye scraper...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = make_context(browser)
        page    = context.new_page()

        login(page)

        seen_ids        = get_seen_ids()
        overnight_items = []

        while True:
            try:
                if not is_logged_in(page):
                    log.warning("Session expired mid-run — re-logging in...")
                    login(page)

                new_items = run_scrape_cycle(page, seen_ids)
                overnight_items.extend(new_items)

                for item in new_items:
                    if item.get("classified_type") == "trade_signal" and \
                       item.get("conviction") in ("Best Idea", "Adding"):
                        send_notification(
                            f"🚨 Hedgeye Signal\n"
                            f"{item.get('direction','Long')} {item.get('ticker','?')} "
                            f"— {item.get('conviction','')}\n"
                            f"{item.get('summary','')[:100]}"
                        )

                if should_send_morning_brief():
                    send_notification(build_morning_brief(overnight_items))
                    mark_morning_brief_sent(datetime.now().date())
                    overnight_items = []
                    log.info("Morning brief sent.")

            except RuntimeError as e:
                log.error(f"Unrecoverable error: {e}")
                break

            except PlaywrightTimeout:
                log.warning("Page timeout — retrying next cycle.")

            except Exception as e:
                log.error(f"Scrape cycle error: {e}")

            log.info(f"Sleeping {SCRAPE_INTERVAL}s until next cycle...")
            time.sleep(SCRAPE_INTERVAL)


if __name__ == "__main__":
    main()
