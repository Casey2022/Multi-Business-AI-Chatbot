# Security pass

Written after the demo tenancy went in, because that change altered what
this app is. Before it, the only people with portal accounts were people I
had created accounts for. After it, any stranger who clicks "Try a demo"
is handed an authenticated session against a real Flask app, with a real
database behind it, and a form on every page. That is a different threat
model, and the code hadn't caught up to it.

The pass went in priority order: what a stranger with a session can reach
first, what leaks without anyone attacking second, and hygiene last.

---

## 1. The secret key could fall back to a known value

`app.secret_key` used to default to the literal string
`dev-secret-change-in-production`, which lives in a public GitHub
repository. The session cookie is the only thing separating a stranger
from the admin portal, and that cookie is signed with this key. Anyone who
read the repo could have forged one claiming to be the operator.

**Now:** `app.py` raises on boot if `SECRET_KEY` is unset, with the
command to generate one in the message. There is no fallback. A deploy
that forgets it fails loudly instead of running insecurely and quietly.

> **Before your next Render deploy:** set `SECRET_KEY` in the environment,
> or the app will refuse to start. Locally, `.env` also needs
> `COOKIE_SECURE=false` — the cookie is marked Secure by default and a
> Secure cookie is never sent over plain `http://127.0.0.1`.

## 2. Nothing checked where a POST came from

Every write in the portal was a plain form with no token. A page on
another origin could have submitted any of them using the visitor's
cookie: change a business's hours, add a knowledge-base section, delete an
appointment.

**Now:** `admin/csrf.py` — a token in the session, a hidden field in every
POST form, and a `before_app_request` hook that compares them with
`compare_digest`. Two endpoints are exempt by name, `sms_reply` and
`webchat_reply`, because they aren't browser forms; Twilio signature
verification is what guards the first. `demo_start` is deliberately *not*
exempt, even though it's the friendliest form on the site.

Hand-rolled rather than Flask-WTF: about forty lines, no new dependency,
and I wanted to understand the mechanism rather than import it.

## 3. The session cookie had no flags

**Now, set explicitly in `app.py`:** `HttpOnly` (script can't read it),
`SameSite=Lax` (it isn't sent on cross-site POSTs, which is a second lock
on the same door as CSRF), `Secure` unless `COOKIE_SECURE=false`, and a
24-hour lifetime via `PERMANENT_SESSION_LIFETIME` — which only applies
because `admin/auth.py` sets `session.permanent = True`. That last line is
the seam: the lifetime silently does nothing without it.

## 4. Customer content was going into the log file

Phone numbers and message bodies were being written at INFO. Logs get
pasted into issues, shipped to log services, and read by whoever has the
dashboard.

**Now, a policy rather than a cleanup:** *structure at INFO, content at
DEBUG*. What happened is loggable; what the customer said is not, unless
someone deliberately turns `LOG_PII=true` on to debug. A
`logging.Filter` in `logging_setup.py` masks phone numbers and email
addresses in anything that slips through, on both handlers.

The filter's first version turned `2026-09-18 16:00` into `…1816:00`. A
redactor that corrupts ordinary data is worse than no redactor, because
it costs you trust in the whole file — so the regexes are narrow now
(explicit `+` and 10–15 digits, or 3-3-4 grouping) and `security_test.py`
checks that timestamps, ZIP codes and token counts come through
untouched.

## 5. Personal data was kept forever

No retention policy at all. Every message, every conversation state row,
every geocode lookup, kept until the disk died.

**Now:** `db.prune_personal_data()` with documented windows — messages 90
days, `conversation_state` 7 days, `geocode_cache` 30 days, each
overridable by environment variable. It runs on the same opportunistic
hook as calendar reconciliation, because a cron job on a free tier is a
cron job that doesn't exist.

**Appointments are deliberately not pruned.** They are the business's own
records, not our copy of the customer's. Deleting them would be data loss
wearing a privacy hat.

## 6. Passwords had no policy — or rather, three of them

`create_user` accepted anything. The CLI had its own 8-character check.
Nothing else checked at all.

**Now:** `db.password_problem(password, email)` returns a human sentence
or `None`, `create_user` raises `ValueError` on a bad one, and all three
call sites go through it. Length-only, 12 characters, overridable —
composition rules push people toward `Password1!` and away from
passphrases, which is the wrong trade.

Because `create_user` now raises, every caller needed an answer for that:
`bootstrap()` catches it and logs, so a weak `ADMIN_PASSWORD` leaves the
portal unseeded instead of taking the app down on boot; the demo generates
a password from `MIN_PASSWORD_LENGTH` rather than a hard-coded length.

## 7. A demo could have overwritten a real client's knowledge base

The one genuine cross-tenant risk. A demo clone *retrieves* from its
template's vector collection — that's what makes cloning a tenant cost
nothing in embedding calls — and ingestion rebuilds whatever collection
the config names. So anything that ingested for a demo before
`fork_collection()` ran would have rebuilt a paying client's knowledge
base out of a stranger's edits.

Two orderings protect it, and orderings are exactly the shape of every bug
in `debugging_lessons.md`, so both are asserted rather than trusted:
`knowledge_publish` forks *before* `load_config` stamps the collection
name onto the config, and the boot loop skips demo clones outright rather
than depending on `sweep_all()` having succeeded (its `try/except`
swallows).

## 8. Customer text went straight into prompts

Every extraction prompt interpolated the customer's message directly
beneath its rules and worked examples. A message carrying a quote and a
newline could close the last example and write its own.

**Now:** `llm.as_data()` fences it in tags, with the closing tag stripped
from the text itself — a fence the customer can close is not a fence — and
a line telling the model the contents are data. Retrieved document
excerpts get the same treatment, because they sit in the *system* prompt,
the most authoritative place text can be, and on a demo tenant anyone can
edit them.

Worth being honest about the size of this one: what a customer could
previously dictate was their own booking. But that booking becomes a row
in the business's database and an entry on the owner's calendar, and
fencing costs three lines.

---

## What was already fine

Not everything needed changing, and it's worth recording what didn't:

- **Authorization.** All 24 routes mapped; every business-scoped one has
  both `login_required` and `require_business_access`. No gaps.
- **Templates.** Jinja autoescaping on everywhere, no `|safe` anywhere.
- **SQL.** Parameterised throughout; no string-built queries.
- **Passwords at rest.** bcrypt (`bcrypt.hashpw` / `checkpw`), never
  anything homemade, and `verify_user` runs a bcrypt check even for an
  unknown email so the timing doesn't say which addresses exist.
- **Twilio.** Signature verification on, `ALLOW_UNSIGNED_REQUESTS`
  defaults to false.
- **Rate limiting.** `ratelimit.py` covers the webchat endpoint, the demo
  start form, and the login form (keyed on both IP and email). bcrypt is
  deliberately slow, so an unthrottled login form is a denial-of-service
  hole as well as a guessing one.

## Still to do

- **`pip-audit`** — has to run on your machine. Neither this shell nor the
  cloud sandbox can reach PyPI or the OSV advisory database, so I couldn't
  do it from here:

  ```
  pip install pip-audit
  pip-audit
  ```

  The installed set is 122 packages and the versions all looked current
  when I read them off `site-packages`, but "looked current" is not an
  audit.

- **A real secrets story.** `.env` on the box is fine for one deploy and
  stops being fine at two.

## How this is kept honest

`security_test.py` — 68 static checks, no server, no browser, no database.
Every one asserts a *relationship*: that the CSRF hook is imported where
it's registered, that every POST form carries a token, that
`prune_personal_data` is called by something outside `db.py`, that the
fork happens before the config load. A protection that isn't attached to
anything protects nothing and says nothing, which is the shape of all four
bugs in `debugging_lessons.md`.
