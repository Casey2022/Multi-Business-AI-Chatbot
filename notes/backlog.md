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

- "opening hours: a day the owner blocked off" passes 1/3 (2026-10-20, same
  before and after the unworded-cancellation change). One miss is the
  model: "we're not open on Sundays" for a one-off block, which
  `policy.one_off_said_as_one` doesn't catch. The other may be the judge:
  "not available this Sunday… open again Tuesday through Sunday" failed.
  Decide who's wrong per run first.
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
- **Authorization:** check every `/business/<id>` route for one owner reading
  or changing another business (an IDOR sweep); operator-only pages; demo
  tenants can't reach real businesses.
- **Web:** no security headers are sent today (CSP, HSTS, frame-ancestors,
  Referrer-Policy); sweep templates for `|safe` and other unescaped output.
- **Abuse and cost:** push on the existing rate limits (chat, demo creation,
  uploads, support); cap model spend per demo visitor.
- **Secrets:** a real secrets story (`notes/security.md`, "Still to do"); the
  SMTP app password lives only in Render's environment.
- **Dependencies:** `pip-audit` before each deploy that changes requirements.
- **Twilio, when it goes live:** test webhook signature validation.

## Bigger features

- **Real Twilio texting:** `notify.py`'s SMS branch becomes a real sender
  (needs texting consent at booking); a phone number recognises returning
  customers ("Booking under Casey again?").
- **Customer self-service cancellation:** blocked on how a web customer
  proves who they are.
- **Optional dark mode:** colour tokens exist, no switch.
