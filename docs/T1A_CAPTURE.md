# Tier1 Alpha daily capture (build list #17)

**What:** Tier1 Alpha's Market Situation Report (MSR) → `t1a_snapshots`, plus
SpotGamma's Founder's Note levels → `sg_index_levels`, compared every morning
in the desk note (🧭 T1A vs SG block) and via `py parser_t1a.py --show`.

**Why it runs on the laptop:** the full MSR is a PDF behind Hedgeye's portal.
It needs Kris's logged-in session **and his home internet**: Cloudflare blocks
datacenter IPs like Railway's. Railway only reads the stored numbers.

## Daily path: Kris's Chrome, run by a scheduled Claude task (weekdays 06:50 ET)
1. `py parser_t1a.py --link` → today's `app.hedgeye.com/ms/msr_2-0?...` URL
   (exits 1 if today's MSR email hasn't landed yet: stop and report).
2. Claude-in-Chrome: open that URL in Kris's Chrome. It redirects to a
   **signed S3 PDF URL valid for 5 minutes**. Read the tab's final URL.
3. Straight away: `curl -s -o data/snapshots/hedgeye/<date>/t1a_msr.pdf '<s3 url>'`
   (same laptop, same home IP). Check the first bytes are `%PDF`.
4. `py parser_t1a.py --render data/snapshots/hedgeye/<date>/t1a_msr.pdf`
   → `_p1` … `_p7.png`. Read all seven and write
   `data/snapshots/hedgeye/<date>/t1a_msr.json` in the full `EXTRACT_PROMPT`
   schema (parser_t1a.py): key levels, short-dated vol term structure + event
   calendar, dealer gamma, systematic/vol-control/CTA + realized-vol drop-off,
   index + sector PV bands, breadth + impact table. Chart-line reads go in
   `approx_fields`; null anything you can't see.
4b. `py -m tools.rv_cross`: our own exact SPX 1m/3m realized vol →
   `spx_realized_vol`, crosses flagged with a ±0.25 hysteresis band, plus a
   flat-market projection of the next cross.
5. `py parser_t1a.py --json <that json> --date <date>`: the validator blocks
   misreads (levels >8% from spot, inverted PV band, unknown status words).
6. `py parser_t1a.py --show --date <date>` → report the block.

Do NOT: sign in (if Hedgeye shows a login page, stop and tell Kris), grant
browser permission prompts, or retry more than once (Cloudflare politeness).

## Notes
- The PDF has no text layer: every value is read off images.
  `sys_positioning_z` is read off a chart line, so it's approximate.
- Unattended API extraction (`--pdf`, `--images`) needs `ANTHROPIC_API_KEY` in
  the laptop's `.env`. It isn't there yet (the key lives in Railway). The
  scheduled Claude session reads the pages itself, so no key is needed.
- Backup path: the bot Chrome on :9222 (`start_hedgeye_chrome.bat`) +
  hedgeye_links.py. It doesn't know `/ms/` links yet and needs a Hedgeye login
  in that profile.
- SG levels: written by the canary's `founders_note.py` after each capture;
  backfill with `py -m tools.sg_index_levels --backfill`.
