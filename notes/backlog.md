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
- "Opening hours: a party tray after closing" (Crosstown), 2/3: one reply
  in three doesn't say "closed" in a way the check reads; read it first.

## Answer quality

- Regular hours misstated after a blocked day: "open again Tuesday through
  Friday 7am-3pm" at Sunrise (open Tue–Sun), journey "a day the owner
  blocked off", 1 run in ~16 on 2026-10-20. A reply check against the
  config's hours would settle it, if it recurs.
- "A day the owner blocked off", ~1 run in 10: the model says "closed on
  Sundays and Mondays", and `policy.one_off_said_as_one` turns it into "not
  available this Sunday and Mondays", which the judge rightly fails.
- Booking notice: adding a new variant from the owner page (version 2 edits
  existing ones only); a negated variant ("not gluten free") still counts
  (`notes/booking_notice.md`).
- Crosstown, Friday 5pm, "noon tray tomorrow, when do I order by?": 1 run
  in 6 (2026-10-22, after the deadline fix) said the right 9:00 AM, then
  "we open at 11, so a noon tray can't be promised; call". Noon is open;
  the chat takes orders before opening.
- Belmont rag_eval row 32 ("do I pay anything up front for balayage"), once
  on 2026-10-23: the $30 deposit right, then "the info I have doesn't confirm
  whether balayage counts as a colour service". It's a lightening service;
  the Colour Deposits section says "colour services over $100".

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

## Owner portal

- A guide page owners can read.

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
- **Abuse and cost:** push on the existing rate limits (chat, demo creation,
  uploads, support). Demo spend is capped (`budget.py`: $0.50 a demo, $5
  all demos, a day), each call at its own model's prices
  (`llm.cost_of`, 2026-10-23). The upload routes' budget checks aren't exercised by
  `budget_test.py`'s sweep (they need a file or a fetched page first);
  `llm._create` still refuses their calls past the budget.
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
