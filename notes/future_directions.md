# Future Directions & Roadmap Considerations

Living document for ideas, capabilities, and architectural concerns that
are out of scope for now but worth preserving so future-me doesn't
backtrack. Add to this whenever a "we might want to do X someday"
conversation happens.

## Voice / phone call support

**Status**: Out of scope for now, but architecturally anticipated.
Likely a real customer request if/when selling to local businesses
(salons, restaurants, medical offices especially).

### Where the current code already works for voice
- The "brain" (rules.py, llm.py, rag.py, scheduler.py, db.py, config.py)
  is channel-agnostic — strings in, strings out. None of it needs to
  change to support a second channel.
- The state machine in scheduler.py is reusable: booking states (idle,
  awaiting_service, awaiting_datetime) work the same whether the input
  came from SMS or transcribed speech.
- Multi-business support (when built) would automatically apply to
  voice too — both channels would look up the right business config.

### Where the current code would need attention
- **System prompt constraints are SMS-shaped.** The "keep under 320
  characters" instruction is wrong for voice (where length is measured
  in spoken seconds, not characters). When voice is added, refactor
  the prompt's guardrails into per-channel layers:
    - SMS: 320 chars, plain text, emojis OK
    - Voice: 1-2 short sentences, no emojis, no markdown, no
      parentheticals or asides
- **Booking flow pacing.** SMS booking is comfortable across 3
  separate messages because customers can pause between them. In
  voice, customers say everything at once ("I want a wedding cake
  for next Saturday at 3pm"). The scheduler should ideally accept
  multi-slot utterances and only fall back to step-by-step when
  some slots are missing. This is a refactor that benefits SMS too.

### What adding voice would actually require
- A `/voice` endpoint in app.py alongside `/sms`
- Twilio `<Gather>` config for speech capture
- Text-to-speech (TTS) for replies — Twilio built-in is OK, paid
  services (ElevenLabs, etc.) sound much better
- Speech-to-text (STT) — Twilio built-in is decent, Deepgram /
  Whisper / AssemblyAI are better and lower-latency
- Interruption ("barge-in") handling
- Latency budget engineering (under 2 seconds end-to-end ideal)
- Voice-specific confirmation flows for ambiguous times ("two" =
  2am or 2pm — ask, don't guess)

### Estimated effort
- **Bare working voice bot**: ~weekend (Twilio docs walk through it)
- **Voice bot that doesn't feel painful**: several weeks of iteration
- **Voice-native (OpenAI Realtime API or similar)**: substantially
  more complex, also more capable; frontier territory

### Cheap intermediate option worth considering
Twilio supports **voicemail-to-SMS** — missed calls get auto-transcribed
and forwarded as an SMS to the existing bot. Adds the "customer called
outside hours" use case without any voice complexity.

### Decision triggers
Add voice when:
- A real customer is asking for it specifically
- The use case is one where calling is preferred (restaurant
  reservations, medical receptionist, salon booking — places where
  customers expect to talk to someone)
- You're ready to commit weeks to it, not just days

Don't add voice because you *can*. SMS is genuinely better for many
small-business use cases (no missed calls, customer reads on their
schedule, no awkward IVR).

## Calendar / order integration for business owners

**Status**: Critical product gap once selling to real businesses.
The bot currently stores appointments in our SQLite, which means the
business owner can't see them. Solving this is core to the product,
not an extra feature.

### Three paths, with trade-offs

**Path A: Sync to the business's existing calendar.** Google Calendar,
Outlook, Apple. The owner sees bookings in the app they already check.
Best long-term product. Real work: each provider has its own API,
plus availability checking (so two customers don't double-book).
Estimated 2-3 days per provider for clean integration.

**Path B: Build our own scheduling dashboard.** Bookings stay in our
DB; owner logs into our portal to see them. Simplest to build (data
is already there), but creates "yet another app to check." Won't work
alone for busy small-business owners. Good *companion* to Path A.

**Path C: Hand off to third-party (Calendly etc).** Bot sends a
booking link instead of conversational booking. Easy to ship —
literally a Calendly URL in business YAML. But abandons the
conversational SMS experience, which is the product's whole appeal.

### Recommended hybrid

- **Path A starting with Google Calendar only.** Covers ~60-70% of
  small businesses. Build other providers as customer demand emerges.
- **Path C as a configurable fallback.** Business YAML can specify
  "send Calendly link" instead of the conversational flow. Useful for
  customers who already have scheduling sorted.
- **Path B for everything that ISN'T calendar.** Message logs, customer
  history, business config editing — those belong in our portal.
  Calendar belongs on the owner's phone.

### Schema implications (capture before forgetting)

When we add Google Calendar integration, the appointments table will
need columns:
- `external_event_id` (e.g., the Google Calendar event ID)
- `external_calendar` (which provider — google, outlook, etc)
- `sync_status` (synced, failed, pending)
- `sync_error` (last error message if failed)

Don't add these empty columns now — adds noise without value. But
know they're coming.

### Design principle for when we build this

The booking confirmation message ("Perfect! Scheduled: drain cleaning
on...") currently fires after the SQLite write succeeds. When external
calendar sync is added, the flow should be:

1. Write to external calendar first
2. On success → write to SQLite + confirm to customer
3. On failure → apologize, offer to call, do NOT pretend it's booked

Half-booked appointments (in our DB but not the owner's calendar) are
worse than no booking at all. Defensive design matters for external APIs.

### Decision triggers

Build Google Calendar integration when:
- A real prospect explicitly asks "can I see these on my phone?"
- More than one business asks how they're supposed to use the bookings
- You're about to take your first paying customer

Don't pre-build all three providers. Pick what your real prospects use.

## Other future ideas (placeholders — flesh out as they come up)

### Multi-business support
The unblocker for hosting many clients on one server. See architectural
notes in the main thread; basically a `businesses` table + `business_id`
column in data tables + per-request config lookup keyed by Twilio number.
Should come before voice. See also notes/rag_improvements.md for why
multi-business should be done before admin UI.

### Admin web page
A UI for non-coders to edit business config (YAML, FAQ, hours, etc).
Should come after multi-business — building it on top of single-business
means rebuilding it later.

track per-business spend. File it in future_directions.md under the admin portal section — showing a business owner "this month you used X API calls" is a useful feature for a paid SaaS product.

### Real Twilio deployment
10DLC registration + deployment to a real host (Render, Railway).
Required for actual paying customers. Mostly DevOps/infra work, not new code.

## Customer-facing website & sales infrastructure

**Status**: Out of scope for now, but anticipated as the project's
go-to-market layer.

### The pieces

1. **Marketing site** — public-facing site with homepage, pricing,
   "try the demo," signup flow. Built separately from the bot
   backend, ideally as a static site or framework like Astro/Next.js
   served from a CDN. Cheap to host, scales to millions of visitors.

2. **Interactive demo widget** — embedded in the marketing site.
   Lets prospects chat with the bot through a web UI before signing
   up. Reuses the existing bot brain via a new web endpoint that
   accepts JSON instead of Twilio form-data. This is the highest-
   leverage marketing feature for a B2B product like this — "try
   before you buy" converts dramatically better than feature lists.

3. **Customer portal / admin** — same as the "admin web page" in
   the main roadmap. Logged-in business owners edit their YAML,
   see appointments, view usage. Sits behind authentication.

4. **Payments via Stripe** — Stripe Checkout for sign-up, Stripe
   Billing for recurring subscriptions. Standard for SaaS.

### Architecture principle

**Keep the marketing site and the bot in separate codebases.**
They have different shapes:
- Marketing site: static, browsed by humans, scales with visitor count
- Bot: dynamic, called by webhooks, scales with active customers

They talk to each other via APIs. Bundling them creates deployment,
security, and scaling headaches that don't need to exist.

### Suggested build order

1. Multi-business support in the bot (already in roadmap)
2. Customer admin portal (already in roadmap)
3. Marketing site + interactive demo widget
4. Stripe integration for paid signup

Each step builds on the prior — no marketing site needed until there's
a product to sign up for; no Stripe needed until there's a signup flow.

### Things to think about now (or soon)

- **Domain name** — buy something before someone else does. Even if
  it sits unused for a year, $15/year is cheap insurance. Worth doing
  before sharing the product publicly.
- **Brand decision** — naming the product separately from "Bob's
  Plumbing's bot." Something a customer of yours would use as a verb.
- **Demo data** — the embedded marketing-site demo needs a believable
  example business. Sunrise Bakery works perfectly for this.

## Long-term customer memory

**Status**: Out of scope for v1. The current sliding-window history
(last 10 messages) is intentionally limited — it keeps prompts cheap
and is enough for typical SMS interactions, but loses names and
preferences across long conversations.

### The trigger to revisit
A real customer complaint or visible bad experience where the bot
forgot something important — name, prior order, allergy, stated
preference. Don't build this preemptively; build when there's a
specific failure case to fix.

### Recommended approach: structured fact extraction
Add a `customer_facts` table:
| phone | key | value | confidence | last_updated |

On each customer turn, run a small LLM extraction call to identify
new facts and upsert into the table. On each LLM-fallback turn,
prepend all known facts for this customer to the system prompt.

### Why this approach over alternatives
- **Bigger window** just delays the problem and inflates costs
- **Prose summarization** loses structure and is harder to surface
  in an admin UI
- **Vector search over history** is overkill for SMS-length conversations
- **Structured facts** are inspectable, queryable, surfacable, and
  fit naturally with the planned admin portal and multi-business work

### Implementation cost estimate
- `db.py`: ~25 lines for the new table + helpers
- `llm.py`: ~30 lines for the extraction call (mirroring parse_datetime)
- `app.py`: ~3 lines to orchestrate

Reuses the same patterns we already have. Real work but bounded.

### Sketches of the extraction call
System prompt:
"You extract durable facts about a customer from a single message.
Output JSON in this shape: {facts: [{key: ..., value: ..., confidence: ...}]}.
Only extract facts that would be useful in future conversations:
name, dates, event type, preferences, allergies, restrictions.
Skip pleasantries, transient feelings, or questions."

After extraction, upsert per (phone, key) — newer values replace older
ones with same key, unless confidence is lower than existing.

## AI usage monitoring dashboard


## Customer-facing appointment management

**Status**: Not yet built. Data exists in SQLite but the bot cannot
surface it back to customers. Belongs after multi-business + admin portal.

### Gap 1 — Appointment status lookup
Customer texts: "what's my appointment?" / "do I have anything booked?"
Currently: no rule catches this; Claude has no DB access; nothing returned.
Fix needed:
- New rule matching "my appointment", "my order", "what did I book", "status"
- Rule handler queries appointments WHERE (phone, business_id), most recent
- Formats result: "Your most recent booking: {service} on {datetime}. Status: {status}"
- Falls back gracefully if no appointments found: "I don't see any bookings
  for your number. Would you like to place one?"

### Gap 2 — Appointment modification and cancellation
Customer texts: "I need to change my appointment" / "cancel my booking"
Currently: no flow exists for modifying existing records.
Fix needed:
- New state machine branch in scheduler.py (or a separate modifier flow)
- States: awaiting_confirm_which → awaiting_new_datetime (or confirm_cancel)
- On confirm: UPDATE appointments SET status='cancelled' or datetime=new_value
- On cancel: UPDATE appointments SET status='cancelled'

### Gap 3 — Long-term customer memory
See separate "Long-term customer memory" section in this file.
Relevant here because appointment modification needs to know which
appointment to modify — customer_facts could store "last booked service"
so the bot doesn't have to ask.

### Build order
1. Appointment status lookup (simpler — just a read)
2. Appointment cancellation (medium — read + update + confirm)
3. Appointment modification (harder — read + new datetime + update + confirm)
4. Long-term memory wired in to assist all three

### Schema already supports this
appointments table has: id, business_id, phone, service, datetime, status
The status field ("booked", future: "confirmed", "cancelled", "completed")
is already there waiting to be used.

Notice-period enforcement on bookings

Status: Not built. Surfaced by a real transcript — an order was accepted at 2pm for 9am the next morning, about 19 hours out, when the documents say standard cupcakes need 24 hours' notice. The bot quoted the policy correctly two messages earlier and then booked in violation of it.

The gap

parse_datetime validates format, not business rules. Nothing in the booking flow checks whether the requested time is far enough out.

The interesting wrinkle

Notice periods currently live only in the RAG documents, which the state machine cannot read — RAG is retrieval for the LLM, not structured data. So enforcement requires the notice period to exist in the YAML config too.

That raises a real design question: do some facts need to live in two places (config for enforcement, documents for conversation), or should config become the source of truth with documents generated from it? Duplication risks drift; a single source requires plumbing. Worth deciding deliberately rather than by accident.

Sketch
yaml
booking:
  notice_hours: 24              # default for this business
  notice_overrides:             # optional per-service
    - match: "custom"           # substring match on the service slot
      hours: 72

Then in _ask_next_or_finalize, after parsing the datetime and before confirming: compare parsed against now + notice_hours. If too soon, don't save — explain the policy and ask for a later time.

Decision trigger

Build when a business would actually be harmed by an under-notice booking, or before any real customer traffic. Currently theoretical harm only.

Customer self-service cancellation and changes

Status: Not built. Currently the bot correctly tells customers to call (guardrail + rule), and the confirmation copy now says "call to change or cancel" — honest, but a real product should let customers do this themselves.

What it needs
Lookup — find the customer's existing bookings. The appointments table is already scoped by (business_id, phone), so the query is easy.
Identity — this is the hard part. See the wrinkle below.
Disambiguation — if a customer has several bookings, ask which one.
Policy check — cancellations inside the notice window may forfeit a deposit (the bakery's documents already spell this out). The bot must state the consequence before acting.
Status update — set status = 'cancelled' rather than deleting the row. The column already exists and the admin page already renders a "Cancelled" badge; it has simply never been used.
The web-identity wrinkle

SMS customers are identified by phone number, which persists across conversations. Web chat visitors get a fresh random web_xxxxxxxx session ID on every page load — so a returning web visitor cannot be matched to an earlier booking at all.

Options, each with a trade-off:

Ask for a phone number during web bookings (adds friction; makes web and SMS customers unifiable, which is valuable on its own)
Persist the session ID in browser storage (fragile; lost on a different device, private window, or cleared storage)
Issue a short confirmation code at booking time and ask for it later (no PII, works anywhere, but the customer has to keep it)

This is a product decision as much as a technical one, and it likely wants solving before long-term customer memory, since both depend on stable identity.

Decision trigger

Build when there is real customer traffic, or when demoing to a prospect who asks "can my customers change their own appointments?"

Slot-filling: possible refinements

The booking flow now uses LLM extraction on every message with a confirmation read-back before saving. Working well. Things noticed but deliberately not built:

Optional slots. Every configured slot is currently required, so a customer must answer "none" to skip. A required: false flag in extra_questions could let the flow skip unanswered optional slots at finalize time.
Extraction cost. One extra Haiku call per mid-booking message. Negligible today; if it ever matters, skip extraction on messages under ~3 words and rely on the existing "treat as current slot" fallback.
Confirmation fatigue. Every booking now ends with a read-back. If customers find it tedious for simple one-slot bookings, it could be skipped when nothing was ever corrected — though the safety argument for always confirming is strong.

## LLM-detected booking intent — replace the keyword trigger + word-count guard with an intent signal from the LLM path, handing off to the scheduler with any extracted slots already filled.

## Google Calendar integration — status and remaining work
Status: Phases 1 and 2 complete. Events write to a business's Google Calendar when a booking is finalized. Tested locally for the success path, the not-configured path, and the failure path.
Decisions made
### Service account, not per-business OAuth. 
The service account has its own identity (...@...iam.gserviceaccount.com); each business shares its calendar with that address and grants "Make changes to events." No consent screen, no redirect handling, no refresh-token storage, no Google app verification process.

Trade-off: onboarding is "share your calendar with this address" rather than a Connect button, and some Workspace domains restrict external sharing. Acceptable for a demo and early clients.

The event-writing code is identical under either auth model — only credential acquisition differs. Swapping to OAuth later means changing one module and adding an admin page, not rewriting the integration.

### Calendar ID lives in business config, not environment. It is per-business data and belongs with the rest of that business's configuration:

calendar:

  enabled: true

  calendar_id: "...@group.calendar.google.com"

  timezone: "America/New_York"

  default_duration_minutes: 60

The service account key stays in the environment (GOOGLE_SERVICE_ACCOUNT_JSON) because it is shared across all businesses and is a secret.

Businesses without a calendar: block are unaffected. is_enabled(config) gates the whole path. Most clients won't have this on day one and the feature must not break them.
## Remaining work
Phase 3 — sync state tracking. Add external_event_id, external_calendar, sync_status, and sync_error to the appointments table. Surface sync status in the admin appointments view so the operator can see which bookings reached the calendar. Small.

Deploy. GOOGLE_SERVICE_ACCOUNT_JSON needs adding to Render's environment variables. Note it is a long single-line JSON string.

Phase 4 — availability checking. Query the calendar's busy times before confirming a slot, so two customers can't book the same time. This is where the real product value is, and it is larger than phases 1–3 combined. Design questions to settle: business hours vs. calendar free/busy as the source of truth, how to offer alternatives when a slot is taken, and whether to hold a slot during the confirmation step.

Cancellation / rescheduling sync. When customer self-service cancellation exists, cancelled bookings must also be removed from the calendar. The external_event_id from Phase 3 is what makes that possible — worth adding before it's needed.

Second calendar for the bakery. Currently only Bob's Plumbing has calendar sync configured. A second calendar shared with the same service account would demonstrate the multi-tenant story properly.

---

## Response quality pass (wanted — not yet scheduled)

Several replies during informal testing didn't flow well. Nothing was
*wrong* exactly; the wording and the handoffs read stiff or off-key. Worth
a dedicated pass rather than fixing one reply at a time as it's noticed.

What makes this tractable: every turn is already in the `messages` table,
role-tagged and business-scoped — 538 rows at the time of writing. That's a
real transcript corpus, not a memory of "one time it said something odd".
The pass should be: pull the assistant turns, read them in conversation
order, and sort the awkwardness into buckets before changing anything.

Likely buckets, to be confirmed against the transcripts rather than assumed:

- **Template seams.** Config-authored strings (`greeting`, `ask_datetime`,
  `final_confirmation`) sitting next to LLM-generated prose. Two voices in
  one conversation, and the joins show.
- **The confirmation summary.** `Just to confirm: X. Service address: Y.
  Problem description: Z. Is that right?` is a form read aloud. Fine for
  correctness, stiff as speech.
- **Handoff into the booking flow.** The moment the assistant stops
  answering and the state machine starts asking.
- **Rejection and fallback wording** — closed, blackout, conflict, bad date.
  These are the replies a frustrated customer sees, so tone matters most
  here and they're the least exercised.
- **Guardrail bleed.** Places where the "I can't do that, call us" rules
  make an otherwise normal answer sound defensive.

Do this *after* file logging, so the odd replies can be traced back to
which path produced them (rules engine, RAG hit, RAG miss, scheduler) —
without that, judging a reply means guessing at where it came from.

---

## Admin UI visual overhaul (wanted)

The portal is functionally complete and looks it: every style is inline,
there's no stylesheet, and the nav markup is copy-pasted into each
template. Fine for building, wrong for a portfolio piece — and the
sandbox demo will put this in front of people whose first judgement is
visual.

Scope when it's picked up:

- Extract the inline styles into a real stylesheet with design tokens
  (colour, spacing, type scale) rather than repeating hex values.
- Replace the duplicated per-page nav with a Jinja macro or a block in
  `base.html`.
- Settle a small component vocabulary — card, table, button, form row,
  flash message — and use it consistently rather than restyling per page.
- Shorten the dashboard action labels.
- Make it legible on a phone; the appointment and conversation tables
  currently assume a wide window.

Worth doing BEFORE the sandbox demo goes public, since the demo's whole
purpose is that someone looks at it. Worth doing AFTER the demo's
tenancy work, because that adds screens (a demo landing page, a reset
banner) that would otherwise need restyling twice.

---

## Conditional booking questions ("ask X only if Y")

Found while building the pizza-shop demo template. Extra questions are
asked every time, unconditionally, so a takeaway bot that needs a
delivery address has no way to skip that question for a pickup order —
and worse, the service-radius check then refuses a pickup customer for
living too far away to deliver to.

Crosstown Pizza ships as delivery-only because of this, with the FAQ
pointing pickup customers at the phone. That's honest but it's a visible
seam in a demo whose job is to look finished.

What it needs: a question that can depend on an earlier answer. The
smallest useful version is one branch — a question with an `ask_if` naming
another question's key and the answers that trigger it, e.g.

    - key: "service_address"
      prompt: "Where are we delivering to?"
      ask_if: {key: "fulfilment", equals: ["delivery"]}

The address slot is injected by the radius setting rather than written by
the owner, so the radius check would have to learn the same condition —
"check the address if we asked for one" rather than "check it if a radius
is set". Those are the same switch today, which is deliberate (it's what
stopped the check silently defaulting to off), so this needs care.

Useful well beyond pizza: any business with a pickup/delivery,
in-person/virtual, or new/returning-customer split.

---

## Prompt caching — checked, and it doesn't apply

Looked at caching the system prompt to cut API spend. It doesn't work here,
and the reason is worth writing down so nobody re-proposes it.

Claude Haiku 4.5 won't cache a prompt shorter than 4,096 tokens. Measured
from the logged request bodies, the largest prompt this app ever sends is
about 1,566 tokens (a Q&A reply with RAG chunks); slot extraction is ~510
and date parsing ~307. Marking those cacheable would be accepted by the API
and silently never cache anything — the worst kind of change, one that
looks like a saving in the diff and is a no-op at runtime.

It only becomes worth revisiting if a prompt grows past 4k, which would
mean either a much longer guardrails block or many more RAG chunks per
reply. Neither is on the roadmap.

The cost picture it turned up instead: roughly $0.001–0.002 per customer
message, one to two cents for a full booking conversation. The spend risk
isn't per-message price, it's a public /demo endpoint multiplied by
strangers — which is what the rate limits, the live-demo ceiling and an
account-level spend cap are for. Per-turn token usage is now logged (see
the `cost` logger), so the question is answerable from the log rather than
from a vendor dashboard the next day.

## Routing to a stronger model with an evaluation model (Jev) — tested, parked

**The question (2026-09-24):** could a cheap "checker" model decide, per
reply, when Haiku's answer is good enough and when to escalate to a
stronger, pricier model? The candidate was TypeSafe AI's Jev, an
*evaluation* model on Vercel AI Gateway: it doesn't write text, it takes a
state plus typed questions and returns probabilities ("does this reply meet
the rubric?" → 0.93).

**Why it was worth asking.** The Haiku vs Sonnet 5 comparison (see the
project's rag-evaluation notes) found Sonnet 5 scored 49/50 to Haiku's
48/50 at ~4x the cost, within run-to-run noise. Upgrading every reply
isn't worth it. Upgrading only the risky replies might be, if something
cheap can tell which ones are risky.

**How it was tested, without committing to it.** Rather than wire Jev into
the live reply path, it was dropped in as the grader for `judge_test.py`:
34 receptionist replies whose correct verdict is already known, most of
them real replies from eval runs. A checker in a cascade does exactly this
job, so its score here predicts how it would do in production. The adapter
is opt-in (`JUDGE_MODEL=typesafe-ai/jev`, commit `ff20845`); nothing else
changed. Total cost: $0 (launch promo), about 600 input tokens per case.

**Result: 33/34**, against 34/34 for the existing judge (Opus 5.5).

- Blunt failures were caught confidently: every false promise, "just call
  us" dodge and wrong cutoff scored 0.01–0.25. That includes every defect
  the receptionist has actually produced (the $110 half-grey quote,
  "gluten-free most days", "cancel by 9am to avoid the fee").
- The one miss was the subtlest reply in the set: a wedding-cake answer
  that states the 7-day rule correctly, then adds one wrong sentence
  ("the deposit is just the 25%"). Jev scored it 0.78; a correct reply
  scored 0.79. No threshold separates them.

**Decision: not adopted.** In a cascade, the checker's misses are the
replies that ship. Jev would catch the obvious errors, which are cheap to
catch other ways (rubric tests, code-enforced rules), and pass the subtle
wrong-fact reply, which is precisely the case a stronger model exists for.
The 34-case set is also too small to trust a 97% figure.

**What would reopen it:** a checker that separates the subtle cases, a
larger labelled set (a few hundred real replies) to measure on, or a
defect rate on Haiku high enough that even catching the blunt failures
pays for itself.

**Interview angle.** The story isn't "I tried a new model." It's:
1. The cost/quality data said a blanket upgrade wasn't worth it, so the
   question became *when* to upgrade, not *whether*.
2. I tested the idea against a set of known answers I already had, in a
   way that touched no production code and cost nothing, before building
   any routing.
3. I looked past the headline number (33/34 sounds great) at *which* case
   failed and what that failure would cost in a cascade. The one miss was
   the most expensive kind.
4. Deciding not to ship something, with evidence written down, is a
   result.
