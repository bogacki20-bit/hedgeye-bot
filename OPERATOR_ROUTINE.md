# OPERATOR ROUTINE — same shape every day

The bot computes and reports; Kris decides. This list is everything the bot
needs FROM Kris so the corpus never falls behind. Fixed daily rhythm; weekly
items appear as extra lines on their day, same time slots.

---

## MORNING — with coffee (~5 min)

1. **Quad ping (6:00am Telegram).** Reply `OK` to confirm, or `QUAD: n n` if
   your read changed. Reply EVERY day — daily confirms are what make the
   corpus labels sharp. (Minimum discipline: never let it go 2 days.)
2. **Scan overnight Telegram.** Anything with 🛑 or ⚠️ or a squawk → screenshot
   it into the next Claude session. Don't debug at 6am; just capture.
3. **Unexpected "Bot started" ping?** If you didn't push code and Railway
   restarted on its own, note the time — that's a crash worth mentioning.

## EVENING — after close (~10 min)

1. **6:00pm checklist ping.** Do what each line says — it tracks PM-upload
   due, anchor age, book age, backlog, doctor status.
2. **Fidelity export, every day:** from the desktop site download BOTH:
   - Positions (`Portfolio_Positions_*.csv`)
   - Accounts History, last ~7 days (`Accounts_History*.csv`)
   into Downloads. Then ONE command:
   ```
   cd C:\Projects\hedgeye-bot
   python _daily_upload.py
   ```
   That ingests the book, feeds the ML trade table, and recomputes outcomes.
   It refuses stale files loudly. Daily uploads = alerts always know your
   true positions (📗 stamps, book screens, RTA cross-signals all read it).
3. **Answer any pending Telegram prompts:** `WRAP OK/NO <ticker>`, alert
   replies (`A<id> BUY/PASS/LATER`), `CONFIRM` gates. Nothing proceeds
   without you — unanswered prompts are silent data loss.

## WEEKLY EXTRAS (same slots, specific days)

- **Monday evening (with the 6pm checklist):** download the Position Monitor
  file → `sync_position_monitor` dry-run → eyeball → CONFIRM. First real
  bucket_history feed of the week.
- **Friday (prompt arrives):** SS anchor — paste the roster, CONFIRM. The
  weekly anchor caps roster drift at one week.
- **Any day the quad changes:** don't wait for morning — `QUAD: n n` the
  moment you've made the call. Event stamps freeze whatever is confirmed at
  write time.

## OCCASIONAL (when prompted, not scheduled)

- **MFR enrollments:** when `MFR BACKLOG` shows names piling up, enroll them
  on the MFR site (enroll-never-remove).
- **New wrapper proposals:** answer `WRAP OK/NO` — never confirm a linkage
  you aren't sure of (the DRIP→EXP lesson: a wrong identity fact poisons
  trend gates).

## DATED (as of 2026-07-11)

- **Mon 7/13:** Feed 2 verdict — compare Monday PM anchor vs the week of
  dry-run deltas before wiring auto-apply.
- **~Jul 17:** option expiries in the book — expiry-watch build not yet
  landed; manual awareness this cycle.
- **~week of 7/13:** review 20/80 alert suppression counts before deciding
  on 15/85 (env flip, no code).
- **Pending Telegram reply:** `WRAP NO DRIP` (the EXP match is a description
  misparse, not Eagle Materials).
