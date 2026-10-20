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
- Holidays the owner hasn't blocked off on the Calendar page aren't known to
  `check_notice` or `add_business_days` (blocked days are).

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

- More journeys: a full booking to confirmation at each business; a question
  mid-booking at each; a Ridgeline business-days conversation; Sunrise's
  "it's 6pm, muffins for 8am" through the live path.

## Security review (before wider use)

The first pass is `notes/security.md` (8 fixes, `security_test.py`). Next:

- **Prompt injection, round 2:** customer chat (role-play, "ignore your
  rules", fake system text, instructions aimed at tool arguments); knowledge-
  base sections an owner or demo visitor writes; uploads (round 1 done:
  hidden text, SSRF); the owner and Help assistants (can they be talked past
  drafts, or into another business?); support messages in the operator inbox.
- **Accounts:** there is no password reset yet. Design it: an emailed
  one-time link, short expiry, rate limit, no "no such user" leak. Changing a
  password should end other sessions. Re-check login throttling.
- **Abuse and cost:** push on the existing rate limits (chat, demo creation,
  uploads, support); cap model spend per demo visitor.
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
