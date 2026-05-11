"""Focused probe for portfolio_solutions / signal_strength / investing_ideas."""
import json, re, sys
import db_pg


PATTERNS = {
    "portfolio_solutions": ["%Portfolio Solutions%", "%Pro Risk Manager%", "%Risk Manager%"],
    "signal_strength":    ["%Signal Strength%", "%SS Signal%"],
    "investing_ideas":    ["%Top Stock Picks%", "%Investing Ideas%", "%Leaderboard%"],
}


def _strip_html(html, n=4000):
    if not html: return ""
    try:
        from bs4 import BeautifulSoup
        s = BeautifulSoup(html, "html.parser")
        for t in s(["style","script"]): t.decompose()
        text = s.get_text("\n", strip=True)
        return re.sub(r"\n{3,}", "\n\n", text)[:n]
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)[:n]


def main():
    out = {}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for product, pats in PATTERNS.items():
                where = " OR ".join(["subject ILIKE %s"] * len(pats))
                cur.execute(
                    f"SELECT message_id, subject, received_at, html_body, sender "
                    f"FROM hedgeye_emails_raw WHERE {where} "
                    f"ORDER BY received_at DESC LIMIT 2",
                    tuple(pats),
                )
                rows = cur.fetchall()
                out[product] = []
                for i, r in enumerate(rows):
                    entry = {
                        "message_id": r[0], "subject": r[1],
                        "received_at": str(r[2]), "sender": r[4],
                    }
                    if i == 0 and r[3]:  # only excerpt the first
                        entry["text_excerpt"] = _strip_html(r[3], 3500)
                    out[product].append(entry)
    print(json.dumps(out, indent=2, default=str), flush=True)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
