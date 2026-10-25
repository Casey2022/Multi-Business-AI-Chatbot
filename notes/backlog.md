# Backlog

Open work, as of 2026-10-20. Done items are removed; their history is in
`git log` and `notes/conversation_defects.md`.

## Now

- **Demo feedback round.** Testers use "Send feedback" (demo chat page) or
  "Help & feedback" (portal); messages land in the operator's Support inbox.
  Feedback is saved whether or not email works.

## Costs money (decided 2026-10-24: later)

- **Email on Render.** The SMTP settings are on Render (`SMTP_HOST`
  smtp.gmail.com, `SMTP_PORT` 587, `SMTP_USER`, `SMTP_PASSWORD` a Gmail app
  password, `SUPPORT_ALERT_EMAIL`), but nothing is sent: a reset request
  logged `Password reset email failed: OSError` (2026-10-24 19:32). Render's
  free web services block outbound ports 25, 465 and 587 (Render changelog,
  2026-09). So reset emails and support alerts don't go out. Two ways:
  - a paid Render instance (the lowest is $25/month as of 2026-10-24): SMTP
    works with no code change, and it's what persistence needs anyway
    (Render's disks are paid only);
  - an email API over https (Brevo, Mailjet, Resend...): free tiers, a code
    change in `support.send_mail`; check each one's sender rules (some need
    a domain of our own).
- **Persistence on Render** (below, under Config structure) needs the same
  paid instance for a disk, or a hosted database.

## Model upgrade (done 2026-10-22)

Receptionist on Claude Haiku 5.5 (a tenth of Haiku 4.5's price), judge on
Claude Sonnet 5.5. Final, Sonnet 5.5 judging: journeys 54/55 x3, rag_eval
--all 154/156, owner assistant 26/29 x3, judge_test 34/34 twice. Left:
- Owner assistant offers support after refusing "publish it yourself and
  make it 24/7", 1-2 runs in 3 (harmless; the eval calls it unnecessary).

## Answer quality

Measured 2026-10-24, 10 runs each, every reply read: the blocked-Sunday
hours slips (Sunrise) and the noon tray (Crosstown) didn't recur, 0/10
each; if one comes back, the journeys are "opening hours: a day the owner
blocked off" and "a tray's order-by time isn't taken back". Belmont's
balayage deposit was hedged 9/10 because the knowledge base didn't say
balayage counts as colour; the seed now does, 10/10 plain.


## Config structure (YAML review, 2026-10-23)

Keep YAML; fix how facts spread across it. Done: opening hours have one
copy (the weekly table; owners edit it on Settings), and each service has
a fixed key, so a rename keeps its links (services.py), and what every
business is told lives once in config/defaults.yaml, and seed_check.py
lists and resets local drift, and config_check.py says what's wrong with a
config in words (startup log, the operator's Businesses page, and
config_check_test.py holds every shipped YAML to none; 2026-10-23). Next:
- **Persistence on Render** before a real business: the disk is wiped on
  each deploy, so owner overrides vanish.


## Uploads

- Keep original uploaded files (plan in `notes/photo_import.md`).
- A scheduled "your website's menu changed" re-check.
- `quote_total`, only if price answers start failing.

## Testing

- Journeys exist for a booking to confirmation, a question mid-booking and a
  correction at the read-back at each business (the day, the time, both,
  the size, the quantity: 15/15 on 2026-10-24, nothing to fix), Ridgeline
  business days, and holidays.

## Security review (before wider use)

The first pass is `notes/security.md` (8 fixes, `security_test.py`). Next:

- **Prompt injection:** round 2 done (`notes/security.md` section 9: 14
  attacks measured, the one that got through fixed). Worth a round 3 when
  real SMS goes live (texts arrive with no page around them) and if the
  owner assistant gains tools beyond drafts.
- **Abuse and cost:** pushed 2026-10-24 (`notes/security.md`, "one visitor,
  many demos"): chat, demo starts and the upload routes' budget checks
  hold; one visitor rotating demos could spend everyone's $5 day, so each
  visitor now has $1 a day (`VISITOR_DAILY_BUDGET`). Left: preview, the
  contradiction check and uploads are still limited per demo, not per
  visitor; the dollar caps bound them.
- **Secrets:** a real secrets story (`notes/security.md`, "Still to do"); the
  SMTP app password lives only in Render's environment.
- **Dependencies:** `pip-audit` before each deploy that changes requirements
  (round 2 done 2026-10-23: 101 advisories, all fixed but chromadb's five
  server-only ones).
- **Twilio, when it goes live:** test webhook signature validation. It
  checks the signature against `request.url`, which on Render is http
  (the https ends at Render's proxy; see "Links in emails were http" in
  `notes/security.md`), while Twilio signs the https address: expect every
  real text to be rejected until the check uses the public https URL.

## Many visitors at once (2026-10-20)

Data doesn't cross between demos (`tenancy_test.py`, `demo_test.py`). What
visitors do share:

- **Four threads: done.** Render's Start Command has `--threads 4`
  (confirmed 2026-10-24; it matches the Procfile, which Render doesn't
  read). Measured live that day: one message alone 1.8-3.3 s; four sent at
  once 3.1-4.3 s each, not the one-at-a-time staircase measured locally
  with one sync worker (2.2, 3.9, 5.9, 7.6 s). The code is ready for
  threads (`threads_test.py`): rate limits, SQLite writes and one message
  in flight per chat page checked. Locally on macOS, `--preload` workers
  segfault on the first outbound call unless run with
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
