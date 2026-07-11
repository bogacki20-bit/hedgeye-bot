import os, psycopg
from dotenv import load_dotenv
load_dotenv()
c = psycopg.connect(os.getenv("DATABASE_PUBLIC_URL"))

# get today's signal strength email
row = c.execute("""
    SELECT message_id, subject, length(html_body)
    FROM hedgeye_emails_raw
    WHERE subject ILIKE '%Signal Strength Stocks%'
    ORDER BY received_at DESC LIMIT 1
""").fetchone()
print("message:", row[1], "| html length:", row[2])

# pull the body and check: do roster names appear as text?
body = c.execute("SELECT html_body FROM hedgeye_emails_raw WHERE message_id=%s",(row[0],)).fetchone()[0]

# strip tags crudely and search for names from the screenshot
import re
text = re.sub(r"<[^>]+>"," ", body)
text = re.sub(r"\s+"," ", text)
for name in ["AMAT","MUSA","CZR","MRVL","CAVA","SBUX","ARCO","GTBIF"]:
    present = bool(re.search(rf"\b{name}\b", text))
    print(f"  {name}: {'FOUND as text' if present else 'not in text (likely image)'}")

# show a chunk of the stripped text so we can eyeball the structure
print("\n--- first 1500 chars of stripped body ---")
print(text[:1500])
c.close()