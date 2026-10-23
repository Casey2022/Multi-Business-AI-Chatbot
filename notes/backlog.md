# Backlog

Open work, as of 2026-10-20. Done items are removed; their history is in
`git log` and `notes/conversation_defects.md`.

## Now

- **Demo feedback round.** Testers use "Send feedback" (demo chat page) or
  "Help & feedback" (portal); messages land in the operator's Support inbox.
  Email alerts are optional: set `SUPPORT_ALERT_EMAIL`, `SMTP_HOST`
  (`smtp.gmail.com`), `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD` (a Gmail
  app password) on Render. Not set up yet; feedback is saved either way.

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
- Crosstown "how much is a large pepperoni": the menu tool says pepperoni
  isn't a listed topping (the menu has "topping", not each one), and the
  model adds $22 + $2.75 itself. Probably right, never looked up; Preview
  now flags "$24.75". Same gap as row 32 below.
- Preview's Now/After comparison (`preview.compare_facts`) got unreadable
  JSON once (2026-10-23) and fell back to "wording differs"; the grounding
  check now reads the object after any preamble, and this could too.
- Local DB: Bob's has a "Drywall Repair" section the seed doesn't (left in
  place on 2026-10-23, when the rest of Bob's was reset to the seed).
- Belmont rag_eval row 32 ("do I pay anything up front for balayage"), once
  on 2026-10-23: the $30 deposit right, then "the info I have doesn't confirm
  whether balayage counts as a colour service". It's a lightening service;
  the Colour Deposits section says "colour services over $100".
- Crosstown rag_eval row 32 ("a medium with two toppings, delivered"):
  "$25-26" for $24.50, once on 2026-10-22; no tool on that path.

## Config structure (YAML review, 2026-10-23)

Keep YAML; fix how facts spread across it. Done: opening hours have one
copy (the weekly table; owners edit it on Settings). Next, in order:
- **Stable service IDs.** Notice, durations, cancellation rules, booking
  questions and menu sections link by service name, and owners can rename
  services on Settings: renaming "custom birthday cakes" silently drops its
  72-hour notice (only a startup log line says so). A fixed key per service,
  the name as a label; until then, warn on Settings before a rename.
- **Shared defaults.** The SMS/voice/webchat channel rules are identical in
  all five YAMLs; the universal guardrails are 24-54% alike. One defaults
  file, each business keeping only what differs (prompts measured the same).
- **Local vs seed check / reset command** (`rag_eval` warns; a command that
  lists and resets the differences would have saved this week's detours).
- **Schema check at load**, surfaced in the portal, not only the log.
- **Persistence on Render** before a real business: the disk is wiped on
  each deploy, so owner overrides vanish.

## Owner portal

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
  all demos, a day), each call at its own model's prices
  (`llm.cost_of`, 2026-10-23). The upload routes' budget checks aren't exercised by
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
