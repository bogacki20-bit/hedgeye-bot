"""Probe hedgeye_emails_raw for product-specific email patterns so we can
design parsers for each.

Outputs subject + sample HTML excerpt for:
  - Portfolio Solutions / Pro Risk Manager
  - Signal Strength
  - Investing Ideas (Top Stock Picks / Leaderboard)
  - Macro Themes (Mid-Quarter Presentation)
  - MOMO Tracker
  - CRYPTO QUANT
"""
import json
import re
import sys
import db_pg


PRODUCT_SUBJECT_PATTERNS = {
    "portfolio_solutions": [
        "%Portfolio Solutions%",
        "%Pro Risk Manager%",
        "%Risk Manager%",
    ],
    "signal_strength": ["%Signal Strength%", "%SS Signal%"],
    "investing_ideas": ["%Top Stock Picks%", "%Investing Ideas%", "%Leaderboard%"],
    "macro_themes":    ["%MACRO THEMES%", "%Macro Themes%"],
    "momo_tracker":    ["%MOMO Tracker%"],
    "crypto_quant":    ["%CRYPTO QUANT%"],
}


def _strip_html(html: str, max_len: int = 4000) -> str:
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["style", "script"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:max_len]
    except Exception:
        stripped = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", stripped).strip()[:max_len]


def main():
    out = {"products": {}}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for product, patterns in PRODUCT_SUBJECT_PATTERNS.items():
                hits = []
                where = " OR ".join(["subject ILIKE %s"] * len(patterns))
                cur.execute(
                    f"SELECT message_id, subject, received_at, sender, "
                    f"       LENGTH(COALESCE(html_body,'')), html_body "
                    f"FROM hedgeye_emails_raw "
                    f"WHERE ({where}) "
                    f"ORDER BY received_at DESC LIMIT 3",
                    tuple(patterns),
                )
                rows = cur.fetchall()
                for r in rows:
                    hits.append({
                        "message_id": r[0],
                        "subject": r[1],
                        "received_at": str(r[2]),
                        "sender": r[3],
                        "html_len": r[4],
                        "text_excerpt": _strip_html(r[5], 3000) if len(hits) == 0 else None,
                    })
                out["products"][product] = hits

    print(json.dumps(out, indent=2, default=str), flush=True)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
