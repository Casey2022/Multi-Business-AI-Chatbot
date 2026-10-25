# Simon Says: an AI receptionist for small businesses

One server, many independent small businesses. Each has its own phone
number, personality, knowledge base, services and booking rules, and is
reachable by SMS or an embedded web chat. Customers ask questions and book;
owners change what the assistant knows and does from their own portal,
without touching code.

**Adding a business is a config file and a document, not a code change.**

## Live demo

**[Try it](https://simonai-q92o.onrender.com/demo)**: pick a business and you
get your own copy of it, both the chat a customer sees and the portal its
owner uses. Change a price or a rule, publish it, and ask the chat again. A
short guided tour points out what's where. Your copy is yours alone and is
cleared when you're done.

Five fictional businesses run on the same server from the same code, each
showing something the others don't:

- **[Bob's Plumbing](https://simonai-q92o.onrender.com/chat/bobs_plumbing)**: one job at a time, travel buffers between them, the customer's address checked against a 20-mile radius, and an after-hours emergency line
- **[Belmont Hair Studio](https://simonai-q92o.onrender.com/chat/belmont_hair_studio)**: three chairs, appointments of different lengths, deposits, and cancellation charges that differ by service
- **[Crosstown Pizza Co.](https://simonai-q92o.onrender.com/chat/crosstown_pizza)**: a menu with sizes and per-topping prices, six orders in the oven at once, and party trays with a 4pm order-by on Fridays and Saturdays
- **[Ridgeline Contracting](https://simonai-q92o.onrender.com/chat/ridgeline_contracting)**: books the free estimate, never the job; counts business days across weekends and holidays
- **[Sunrise Bakery & Café](https://simonai-q92o.onrender.com/chat/sunrise_bakery_and_cafe)**: orders rather than appointments, notice periods per product (gluten-free needs four days, "not gluten free" doesn't)

<img width="357" height="497" alt="Bob&#39;s Plumbing" src="https://github.com/user-attachments/assets/110aa1d6-801e-4d55-80dc-c1651769ff20" /> <img width="357" height="497" alt="Sunrise Bakery" src="https://github.com/user-attachments/assets/0f36852d-64b2-46f5-8633-371e44bbc2b3" />

Try asking about prices, something they *don't* offer, or a time that's too
soon; or say "book" / "order" to start a booking and change your mind at the
read-back ("actually make it Thursday").

> Hosted on Render's free tier, which sleeps after 15 minutes idle: the first
> request can take up to a minute to wake it. The free tier also wipes the
> disk on each deploy, so the live site rebuilds from the seed files.

## What it does

**For customers**
- Answers questions from the business's own knowledge base, and says so
  when it doesn't know, with a way forward (the phone number) instead of a
  guess.
- Takes bookings in conversation: every detail at once or one at a time,
  corrections at any point, questions mid-booking. It checks opening hours,
  blocked days, holidays, capacity, travel buffers, booking notice and the
  service area before it confirms anything.
- Works the same over SMS (Twilio) and web chat.

**For owners** (the portal, `/admin`)
- **Knowledge base, Menu, Cancellation rules, Booking notice**: every
  change is a draft. **Preview answers** shows how the assistant would
  answer before and after, flagging facts that changed and claims nothing
  supports; **Publish** makes it live.
- **Update from a photo, PDF or web page**: the assistant reads a menu or
  price list and proposes the changes, for the owner to tick.
- **Check for contradictions** across sections and settings.
- **Calendar**: blocked time, holidays, and every booking; Google Calendar
  sync.
- An **owner assistant** that answers "how do I…?" from the owner guide
  (also a page of its own) and can make knowledge-base and menu edits as
  drafts.

**For the operator**: every business at a glance, with any mistake in its
config said in words, and a support inbox. Demo visits are counted in
Cloudflare Web Analytics.

## How it works

```
Incoming message (SMS or web)
   │
   ├─ 1. Keyword rules ──────── instant canned replies (hours, greetings)
   │
   ├─ 2. Booking flow ───────── a deterministic state machine; the model
   │                            only extracts what the customer said
   │
   └─ 3. Model + retrieval ──── the business's own documents, plus tools
                                that work out facts in code (notice,
                                cancellation charges, business days, prices)
                                   │
                                   └─ reply checks: deterministic fixes for
                                      what prompting alone didn't stop
```

**The model phrases; code settles facts.** Arithmetic and rule lookups
(how much notice a gluten-free cake needs, what cancelling 30 hours ahead
costs, the price of a medium with two toppings) are tools in code, and
facts the customer stated are read in code before a tool runs. The model
turns the answer into a sentence.

**Each layer gets the authority it can be trusted with.** The model
extracts and suggests; the state machine decides and writes. A booking is
only confirmed after the database write that makes it true.

**Prompt rules alone don't hold.** When the model kept producing the same
wrong phrase ("you still have time" after a free window had passed, a
discount someone claimed promised back), a check on the finished reply
fixes it, rather than one more instruction.

**One source of truth.** Prices live only in the menu, cancellation rules
only in the rules, opening hours only in the weekly table; the knowledge
base gets generated lines from them, so two copies can't disagree.

## How it's tested

- **`run_tests.py`**: about 2,700 offline checks across 55 files (~90s, no
  API calls). Every new guard is mutation-tested: break it on purpose,
  confirm a test fails, restore.
- **Evals against the live model** (a few cents each), with a judge model
  grading meaning against a rubric rather than matching wording:
  `journey_eval.py` (79 whole conversations through the real message path),
  `rag_eval.py` (retrieval and answers per business), `qa_eval.py`,
  `conversation_eval.py`, `grounding_eval.py`, `contradiction_eval.py`,
  `upload_eval.py`, `assistant_eval.py`.
- **Measure first.** A reported failure becomes an eval row or journey
  before the fix, and the fix is judged by re-running it. When an eval
  fails, the first question is whether the bot or the check is wrong.

## Tech stack

**Backend:** Python 3.13, Flask, SQLite, gunicorn
**AI:** Anthropic Claude (Haiku 5.5 for replies; Sonnet 5.5 for judging, grounding and contradiction checks), Voyage AI embeddings
**Retrieval:** ChromaDB, one collection per business
**Integrations:** Twilio (SMS, signature-verified), Google Calendar, Cloudflare Web Analytics (cookieless, demo pages only)
**Frontend:** Jinja2 templates, vanilla JavaScript as progressive enhancement
**Hosting:** Render

## Multi-tenancy

Every request resolves to a business first: the Twilio `To` number for SMS,
the URL slug for web chat, the signed-in owner for the portal. Its config
(the YAML, with the owner's saved changes on top) is loaded per request and
passed explicitly. Messages, appointments, conversation state and the
knowledge-base collection are all scoped to the business, and
`tenancy_test.py` checks that one demo visitor can't read or change
another's data through any portal route.

## Adding a business

1. Create `config/<slug>.yaml`: business details, services, opening hours,
   persona, booking questions, notice, menu, cancellation rules. Shared
   wording every business gets lives once in `config/defaults.yaml`.
2. Create `documents/<slug>/services.md`: what the assistant should know.
3. Add the business to the list in `seed_businesses.py`.
4. Restart. On boot the app registers it, imports its knowledge base and
   builds its search index.

`run_tests.py` holds every shipped config to `config_check.py` (a typo'd
time zone or hours written 17:00-09:00 fail before they're committed), and
the operator's Businesses page shows the same check. Full runbook:
[`notes/adding_a_new_business.md`](notes/adding_a_new_business.md).

## Configuration example

```yaml
business:
  name: "Sunrise Bakery & Café"
  phone: "(585) 555-0188"

services:
  - "custom birthday cakes"
  - "muffins by the dozen"

calendar:
  timezone: "America/New_York"
  scheduling:
    business_hours:            # 0 = Monday; the hours customers hear are written from this
      1: ["07:00", "15:00"]
      # ...

booking:
  noun: "order"                # Bob's Plumbing says "appointment"
  notice:
    muffins by the dozen: {hours: 24, variants: [
      {words: [gluten free], hours: 96, name: "gluten-free muffins"}]}

bot:
  persona_preset: "warm"
  guardrails:
    universal:
      - rule: facts_only       # shared wording, from config/defaults.yaml
```

## Running locally

```bash
git clone https://github.com/Casey2022/Multi-Business-AI-Chatbot.git
cd Multi-Business-AI-Chatbot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

A `.env` file:

```
ANTHROPIC_API_KEY=your-key
VOYAGE_API_KEY=your-key
SECRET_KEY=generate-with-secrets.token_hex(32)
ADMIN_EMAIL=you@example.com
ADMIN_PASSWORD=at-least-12-characters
ALLOW_UNSIGNED_REQUESTS=true    # local only: lets curl tests skip Twilio signatures

# Optional
SMTP_HOST=smtp.gmail.com        # password-reset emails and support alerts
SMTP_PORT=587                   # (Render's free tier blocks these ports)
SMTP_USER=you@gmail.com
SMTP_PASSWORD=a-gmail-app-password
SUPPORT_ALERT_EMAIL=you@example.com
CF_WEB_ANALYTICS_TOKEN=...      # Cloudflare Web Analytics, demo pages only
```

Then `python3 app.py` and open `http://127.0.0.1:5001/demo` (port 5001, not
5000: macOS uses 5000 for AirPlay).

| path | what it is |
|---|---|
| `/demo` | the sandbox: pick a business and get your own copy |
| `/chat/<slug>` | the chat page a customer sees |
| `/chat/<slug>/text` | the same assistant as texts on a drawn phone: the real SMS path, segment counts, nothing sent |
| `/admin` | the owner's portal (and the operator's Businesses page) |
| `/admin/guide` | the owner guide, the same text the owner assistant answers from |
| `/admin/help` | ask the owner assistant, or a person |
| `/sms` | the Twilio webhook |
| `/version` | the deployed commit |

```bash
python3 run_tests.py            # every offline test file (~90s, no API calls)
python3 run_tests.py menu       # only files whose names match
python3 journey_eval.py         # whole conversations against the live model
python3 seed_check.py           # where the local database differs from the seed
```

## Project structure

```
app.py, conversation.py   routes and channels; process_message, the path every message takes
llm.py                    prompts, the tool loop, reply checks
scheduler.py              the booking state machine
policy.py, menu.py        rule tools (notice, cancellation, business days) and menu prices
rag.py                    ingestion and retrieval
config.py, config_check.py, services.py, section_links.py
                          loading config, checking it, and keeping links through renames
preview.py, grounding.py, contradictions.py
                          Preview answers and the checks behind it
photo_import.py, web_import.py
                          reading photos, PDFs and web pages (with SSRF guards)
assistant.py              the owner assistant
budget.py, ratelimit.py   the demo's spending caps and rate limits
admin/                    the portal (routes, templates, owner_guide.md)
config/, documents/       per-business YAML and knowledge bases
notes/                    design decisions, debugging lessons, security, backlog
```

A guided read: [`notes/codebase_walkthrough.md`](notes/codebase_walkthrough.md).

## Engineering notes

A few decisions, each from something that went wrong:

**Grounded confirmation.** An early version, told about the booking process
so it could answer questions about it, started *performing* bookings,
confirming orders that were never saved. Confirmations now come only from a
successful database write.

**Retrieval that admits ignorance, and remembers.** A distance threshold
lets the bot say "I don't have details on that" rather than assemble an
answer from weak matches. And each turn now also sees the excerpts behind
the customer's last two questions: without them, the model took back its
own true answers a turn later ("Correction: disregard the prices I listed
earlier"), in 5 conversations out of 20.

**Measure, then decide who's wrong.** Belmont's balayage deposit was
hedged 9 times in 10. The bot was right to hedge: the knowledge base never
said balayage was a colour service. The fix was one sentence in the
document, not the prompt.

**The proxy's header, read from the right.** Rate limits keyed on the first
`X-Forwarded-For` entry, which the visitor writes. Measured on Render, the
real address is third from the right; read from there, a made-up header no
longer resets a limit.

**One visitor can't spend everyone's day.** The demo has a daily model
budget. A test with one address starting demo after demo spent all of it in
8 demos; each visitor now has a budget of their own.

**Fork-safe initialization.** The vector database client is created lazily
per process: created at import time, gunicorn's forked workers inherited
locks from a thread that didn't exist in them and deadlocked. Invisible
locally; fatal in production.

More in [`notes/debugging_lessons.md`](notes/debugging_lessons.md) and
[`notes/security.md`](notes/security.md).

## Roadmap

- A paid Render instance: a disk that survives deploys (owner changes are
  wiped today) and outbound email (reset links, support alerts)
- Real Twilio texting, with returning customers recognised by number
- Customer self-service cancellation and rescheduling
- A voice channel: the channel adapters already anticipate it

Details and reasoning: [`notes/backlog.md`](notes/backlog.md) and
[`notes/future_directions.md`](notes/future_directions.md).

## Status

Working end to end and deployed. A portfolio project built with an eye to
real commercial use.
