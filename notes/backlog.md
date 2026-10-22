# Backlog

Open work, as of 2026-10-20. Done items are removed; their history is in
`git log` and `notes/conversation_defects.md`.

## Now

- **Demo feedback round.** Testers use "Send feedback" (demo chat page) or
  "Help & feedback" (portal); messages land in the operator's Support inbox.
  Email alerts are optional: set `SUPPORT_ALERT_EMAIL`, `SMTP_HOST`
  (`smtp.gmail.com`), `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD` (a Gmail
  app password) on Render. Not set up yet; feedback is saved either way.

## Answer quality

- Regular hours misstated after a blocked day: "open again Tuesday through
  Friday 7am-3pm" at Sunrise (open Tue–Sun), journey "a day the owner
  blocked off", 1 run in ~16 on 2026-10-20. A reply check against the
  config's hours would settle it, if it recurs.
- "A day the owner blocked off", ~1 run in 10: the model says "closed on
  Sundays and Mondays", and `policy.one_off_said_as_one` turns it into "not
  available this Sunday and Mondays", which the judge rightly fails.
- **Booking notice, after version 1** (`notes/booking_notice.md`). The
  table is the only copy now: each section's notice line is generated from
  `booking.notice`, the prose no longer states the numbers, and
  `check_notice` takes them from the table (as a floor). Still to do:
  (1) variants: themed cupcake displays 72h, gluten-free 96h, still in the
  prose; (2) an owner page to edit notice (the Knowledge page shows it
  read-only).
- "Friday at five, food for twenty" (Crosstown) slid to 3/6 and 4/6 on
  2026-10-22 (with and without that day's change; 6/6 on 10-20). The runs
  show no check_notice call: the model now answers without checking, and
  invites "check if a tray might still work". Same model snapshot.
- Crosstown rag_eval row 32 ("a medium with two toppings, delivered"):
  "$25-26" for $24.50, once on 2026-10-22; no tool on that path.
- A slot answered out of turn gets the same question again word for word
  (Ridgeline: scope given while asked for timing, "Any timeframe in mind?"
  twice). Correct, but reads as a bot that didn't listen.

## Owner portal

- A "not in your documents" flag in Preview answers: a fact the assistant
  states that the knowledge base doesn't contain.
- A contradiction check across knowledge-base sections.
- A guide page owners can read.
- The Knowledge page's rename doesn't warn that menu prices attach by section
  title (the owner assistant refuses that rename; the page doesn't).

## Uploads

- Keep original uploaded files (plan in `notes/photo_import.md`).
- A scheduled "your website's menu changed" re-check.
- `quote_total`, only if price answers start failing.

## Testing

- Journeys exist for a booking to confirmation and a question mid-booking at
  each business, Ridgeline business days, and holidays (2026-10-20). Next:
  a correction at the read-back ("actually make it Thursday") at each.

## Security review (before wider use)

The first pass is `notes/security.md` (8 fixes, `security_test.py`). Next:

- **Prompt injection:** round 2 done (`notes/security.md` section 9: 14
  attacks measured, the one that got through fixed). Worth a round 3 when
  real SMS goes live (texts arrive with no page around them) and if the
  owner assistant gains tools beyond drafts.
- **Accounts:** there is no password reset yet. Design it: an emailed
  one-time link, short expiry, rate limit, no "no such user" leak. Changing a
  password should end other sessions. Re-check login throttling.
- **Abuse and cost:** push on the existing rate limits (chat, demo creation,
  uploads, support). Demo spend is capped (`budget.py`: $0.50 a demo, $5
  all demos, a day). It prices every call at `LLM_PRICE_IN`/`OUT`, so if
  `ASSISTANT_MODEL` or `UPLOAD_MODEL` is set to a pricier model, those calls
  are under-counted. The upload routes' budget checks aren't exercised by
  `budget_test.py`'s sweep (they need a file or a fetched page first);
  `llm._create` still refuses their calls past the budget.
- **Secrets:** a real secrets story (`notes/security.md`, "Still to do"); the
  SMTP app password lives only in Render's environment.
- **Dependencies:** `pip-audit` before each deploy that changes requirements.
- **Twilio, when it goes live:** test webhook signature validation.

## Many visitors at once (2026-10-20)

Data doesn't cross between demos (`tenancy_test.py`, `demo_test.py`). What
visitors do share:

- **One request at a time → four threads.** Render's Start Command was
  `gunicorn app:app --workers 1 --timeout 60 --bind 0.0.0.0:$PORT`: one
  sync worker. Measured locally with Render's settings, four visitors
  sending at once waited 2.2, 3.9, 5.9, 7.6 s (one alone: 2.4 s). With
  `--threads 4`: about 2 s each, twice. The code is ready for threads
  (`threads_test.py`); **to do: add `--threads 4` to Render's Start
  Command** (the Procfile has it, but Render doesn't read the Procfile).
  Checked and fine with threads: rate limits (SQLite, atomic increment),
  SQLite (800 writes from 8 threads, no lock errors), one message in
  flight per chat page. Locally on macOS, `--preload` workers segfault on
  the first outbound call unless run with
  `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES no_proxy='*'`; Render (Linux)
  doesn't need this.

Decided, 2026-10-20: the per-IP limits (3 demo starts a minute from one
Wi-Fi) and the 50-demo ceiling are fine as they are.

## Bigger features

- **Real Twilio texting:** `notify.py`'s SMS branch becomes a real sender
  (needs texting consent at booking); a phone number recognises returning
  customers ("Booking under Casey again?").
- **Customer self-service cancellation:** blocked on how a web customer
  proves who they are.
- **Optional dark mode:** colour tokens exist, no switch.
