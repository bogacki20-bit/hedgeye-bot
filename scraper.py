"""
Hedgeye Portal Scraper
Logs into app.hedgeye.com, scrapes the feed every 15 minutes,
extracts content and passes to classifier.
"""

import os
import time
import json
import logging
import hashlib
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from classifier import classify_and_extract
from notifier import send_text
from database import save_item, get_seen_ids, mark_morning_brief_sent, was_morning_brief_sent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

HEDGEYE_EMAIL    = os.environ["HEDGEYE_EMAIL"]
HEDGEYE_PASSWORD = os.environ["HEDGEYE_PASSWORD"]
SCRAPE_INTERVAL  = int(os.getenv("SCRAPE_INTERVAL_SECONDS", "900"))  # 15 min default
MORNING_BRIEF_HOUR = int(os.getenv("MORNING_BRIEF_HOUR", "7"))       # 7am ET


def login(page):
    """Authenticate with Hedgeye - tries multiple selectors."""
    log.info("Logging into Hedgeye...")
    page.goto("https://app.hedgeye.com/users/sign_in", wait_until="domcontentloaded")
    time.sleep(3)

    # Try multiple selectors for email field
    for sel in [
        'input[name="user[email]"]',
        'input[type="email"]',
        'input[id="user_email"]',
        'input[placeholder*="mail" i]',
        'input[autocomplete="email"]',
    ]:
        try:
            page.wait_for_selector(sel, timeout=5000)
            page.fill(sel, HEDGEYE_EMAIL)
            log.info(f"Email filled using: {sel}")
            break
        except Exception:
            continue

    # Try multiple selectors for password field
    for sel in [
        'input[name="user[password]"]',
        'input[type="password"]',
        'input[id="user_password"]',
        'input[autocomplete="current-password"]',
    ]:
        try:
            page.wait_for_selector(sel, timeout=5000)
            page.fill(sel, HEDGEYE_PASSWORD)
            log.info(f"Password filled using: {sel}")
            break
        except Exception:
            continue

    # Submit the form
    for sel in [
        'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("Sign in")',
        'button:has-text("Log in")',
        'button:has-text("Login")',
        '.btn-primary',
        '[data-testid="login-button"]',
    ]:
        try:
            page.click(sel, timeout=5000)
            log.info(f"Submitted using: {sel}")
            break
        except Exception:
            continue

    try:
        page.wait_for_url("**/feed_items**", timeout=20000)
        log.info("Login successful.")
    except Exception:
        time.sleep(3)
        current_url = page.url
        log.info(f"Post-login URL: {current_url}")
        if "sign_in" in current_url:
            log.error("Still on login page - check credentials")
        else:
            log.info("Login appears successful")

def scrape_feed(page) -> list[dict]:
    """Scrape the main feed and return list of raw items."""
    log.info("Scraping feed...")
    page.goto("https://app.hedgeye.com/feed_items", wait_until="networkidle")
    time.sleep(2)  # let JS render

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

            # Absolute URL
            if link and not link.startswith("http"):
                link = "https://app.hedgeye.com" + link

            # Unique ID based on link or content hash
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
    """Follow the item link and grab full article text."""
    if not item.get("link"):
        return item

    try:
        page.goto(item["link"], wait_until="networkidle", timeout=20000)
        time.sleep(1)

        # Try multiple selectors for article body
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
    """Check if it's time to send the morning brief and we haven't sent one today."""
    now = datetime.now()
    if now.hour == MORNING_BRIEF_HOUR and not was_morning_brief_sent(now.date()):
        return True
    return False


def build_morning_brief(new_items: list[dict]) -> str:
    """Build a concise morning text message from overnight items."""
    if not new_items:
        return "Hedgeye morning brief: No new signals overnight. Check portal for updates."

    signals    = [i for i in new_items if i.get("classified_type") == "trade_signal"]
    macro      = [i for i in new_items if i.get("classified_type") == "market_situation"]
    research   = [i for i in new_items if i.get("classified_type") == "sector_research"]
    other      = [i for i in new_items if i.get("classified_type") not in ("trade_signal","market_situation","sector_research")]

    lines = [f"📊 Hedgeye Morning Brief — {datetime.now().strftime('%b %d')}"]

    if signals:
        lines.append(f"\n🟢 SIGNALS ({len(signals)}):")
        for s in signals[:5]:  # cap at 5 to keep SMS readable
            ticker     = s.get("ticker", "?")
            conviction = s.get("conviction", "")
            direction  = s.get("direction", "Long")
            lines.append(f"  {direction} {ticker} — {conviction}")

    if macro:
        lines.append(f"\n📈 MACRO ({len(macro)}):")
        for m in macro[:2]:
            summary = m.get("summary", m.get("title",""))[:80]
            lines.append(f"  {summary}")

    if research:
        lines.append(f"\n🔬 RESEARCH: {len(research)} new notes")

    if other:
        lines.append(f"\n📋 OTHER: {len(other)} items")

    lines.append("\napp.hedgeye.com")
    return "\n".join(lines)


def run_scrape_cycle(page, seen_ids: set) -> list[dict]:
    """One scrape cycle — returns newly seen items."""
    raw_items   = scrape_feed(page)
    new_items   = [i for i in raw_items if i["id"] not in seen_ids]

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
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = context.new_page()

        login(page)
        seen_ids       = get_seen_ids()
        overnight_items = []

        while True:
            try:
                new_items = run_scrape_cycle(page, seen_ids)
                overnight_items.extend(new_items)

                # Send immediate alert for high-conviction trade signals
                for item in new_items:
                    if item.get("classified_type") == "trade_signal" and \
                       item.get("conviction") in ("Best Idea", "Adding"):
                        msg = (
                            f"🚨 Hedgeye Signal\n"
                            f"{item.get('direction','Long')} {item.get('ticker','?')} "
                            f"— {item.get('conviction','')}\n"
                            f"{item.get('summary','')[:100]}"
                        )
                        send_text(msg)

                # Morning brief
                if should_send_morning_brief():
                    brief = build_morning_brief(overnight_items)
                    send_text(brief)
                    mark_morning_brief_sent(datetime.now().date())
                    overnight_items = []
                    log.info("Morning brief sent.")

            except PlaywrightTimeout:
                log.warning("Page timeout — re-logging in...")
                try:
                    login(page)
                except Exception as e:
                    log.error(f"Re-login failed: {e}")

            except Exception as e:
                log.error(f"Scrape cycle error: {e}")

            log.info(f"Sleeping {SCRAPE_INTERVAL}s until next cycle...")
            time.sleep(SCRAPE_INTERVAL)


if __name__ == "__main__":
    main()

