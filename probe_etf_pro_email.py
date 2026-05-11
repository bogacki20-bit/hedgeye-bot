"""Read the most recent ETF Pro Plus weekly email and parse the body to find the range table.

Uses BeautifulSoup to navigate past the CSS prelude and pull the table.
"""
import json
import re
import sys
import db_pg


def main():
    out = {}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT message_id, subject, received_at, sender, html_body
                  FROM hedgeye_emails_raw
                 WHERE subject ILIKE '%ETF Pro Plus%'
                   AND subject ILIKE '%Weekly%'
                 ORDER BY received_at DESC
                 LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                out["error"] = "no ETF Pro Plus weekly email found"
                print(json.dumps(out, indent=2)); return
            msg_id, subj, recv, sender, hbody = row
            out["message_id"] = msg_id
            out["subject"] = subj
            out["received_at"] = str(recv)

            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(hbody, "html.parser")
            except ImportError:
                out["error"] = "BeautifulSoup not installed"
                print(json.dumps(out, indent=2)); return

            # Strip <style> and <script>
            for tag in soup(["style", "script"]):
                tag.decompose()

            # Get visible text
            text = soup.get_text("\n", strip=True)
            # Collapse runs of newlines
            text = re.sub(r"\n{3,}", "\n\n", text)
            out["body_text_len"] = len(text)
            out["body_text_excerpt"] = text[:6000]

            # Look for tables — the range table is likely an HTML <table>
            tables = soup.find_all("table")
            out["table_count"] = len(tables)
            # Find tables that mention common ETF tickers (SPY, QQQ, XLK)
            range_tables = []
            for i, t in enumerate(tables):
                tt = t.get_text(" ", strip=True)
                if any(tk in tt for tk in ("SPY", "QQQ", "XLK", "XLE", "OIH", "TLT")):
                    range_tables.append({
                        "idx": i,
                        "text_len": len(tt),
                        "text_preview": tt[:1200],
                    })
            out["candidate_range_tables"] = range_tables[:5]

    print(json.dumps(out, indent=2, default=str), flush=True)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
