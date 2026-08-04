# Multi-Business AI Chatbot

An AI receptionist platform for small businesses. One server hosts many
independent client businesses — each with its own phone number, personality,
document knowledge base, and booking flow — reachable by SMS or embedded web
chat, with an admin portal for managing conversations and appointments.

**Adding a new client is a config file and a document, not a code change.**

## Live demo

Two fictional businesses running on the same server, from the same codebase:

- **[Bob's Plumbing](https://simonai-q92o.onrender.com/demo/bobs_plumbing)** — a service business with emergency calls and job scheduling
- **[Sunrise Bakery & Café](https://simonai-q92o.onrender.com/demo/sunrise_bakery_and_cafe)** — a retail bakery with custom orders and dietary questions

- [Demo]
- <img width="357" height="497" alt="Bob&#39;s Plumbing" src="https://github.com/user-attachments/assets/110aa1d6-801e-4d55-80dc-c1651769ff20" /> <img width="357" height="497" alt="Sunrise Bakery" src="https://github.com/user-attachments/assets/0f36852d-64b2-46f5-8633-371e44bbc2b3" />

Try asking about prices, services they *don't* offer, or say "order" / "book"
to start a booking. Notice that the two bots have different personalities,
different knowledge, and even call a booking by a different name.

> Hosted on a free tier that sleeps after 15 minutes of inactivity — the first
> request may take up to a minute to wake the server.

## What it does

- **Answers questions** grounded in each business's own documents, with an
  explicit "I don't know" path rather than invented answers
- **Takes bookings** through natural conversation — customers can give every
  detail at once or one at a time, and correct themselves mid-flow
- **Serves many businesses** from one deployment, routed by inbound phone
  number (SMS) or URL slug (web)
- **Works across channels** — Twilio SMS and web chat share the same core,
  with per-channel behavioural rules
- **Gives the operator visibility** — a password-protected admin portal with
  conversation threads and an appointments dashboard

## Architecture

Three response layers, each handling what it's best suited for:

```
Incoming message
   │
   ├─ 1. Rules engine ──────── keyword match → instant canned reply
   │                            (free, ~1ms, handles greetings/hours/location)
   │
   ├─ 2. Booking state machine  if mid-booking, every message is an answer
   │                            (deterministic; owns all database writes)
   │
   └─ 3. LLM + RAG ─────────── semantic search over business documents,
                                then Claude generates a grounded reply
```

Each layer exists because the one before it had a specific limitation. Rules
are free and instant but can't handle anything unanticipated. RAG grounds
answers in real documents but can't take an action. The LLM converses
naturally but cannot be trusted to touch the database.

**The design principle:** each layer gets exactly the authority it can be
trusted with. The language model extracts structured data from conversation
and suggests; a deterministic state machine decides and writes. A confirmation
message is only ever produced *after* a successful database write — so the bot
cannot tell a customer their order is placed unless an order actually exists.

### Channel adapters

```
SMS (Twilio)  ─┐
Web chat      ─┼─→  process_message()  →  rules / booking / LLM+RAG
Voice (future)─┘         (channel-agnostic core)
```

Each channel is a thin endpoint that translates its own format into a standard
internal call and formats the reply on the way out. Adding a channel is ~20
lines and does not touch the core.

## Tech stack

**Backend:** Python 3.13, Flask, SQLite
**AI:** Anthropic Claude (conversation, structured extraction), Voyage AI (embeddings)
**Retrieval:** ChromaDB with per-business collections
**Messaging:** Twilio (SMS webhooks, signature verification)
**Frontend:** Jinja2 templates, vanilla JavaScript
**Deployment:** Render, gunicorn

## How multi-tenancy works

A `businesses` table maps identifiers to configuration:

| id | name | slug | twilio_number | config_path |
|----|------|------|---------------|-------------|
| 1 | Bob's Plumbing | `bobs_plumbing` | +1585... | `config/bobs_plumbing.yaml` |
| 2 | Sunrise Bakery & Café | `sunrise_bakery_and_cafe` | +1585... | `config/sunrise_bakery_and_cafe.yaml` |

Every request resolves to a business before anything else happens — by the
Twilio `To` number for SMS, by URL slug for web chat. Config is then loaded
per-request and threaded explicitly through every function. Messages,
appointments, and conversation state are all scoped by `business_id`, so two
businesses can serve the same customer independently without collision.

## Adding a new business

No code changes required:

1. **Create `config/<slug>.yaml`** — business facts, services, persona,
   behavioural guardrails, keyword rules, and booking questions
2. **Create `documents/<slug>/services.md`** — the knowledge base the bot
   answers from (prices, policies, what you don't offer)
3. **Register it** in the `businesses` table
4. **Ingest** the documents: `python3 rag.py`
5. **Restart** the server

See [`notes/adding_a_new_business.md`](notes/adding_a_new_business.md) for the
full runbook, templates, and troubleshooting table.

## Configuration example

Business behaviour lives entirely in YAML:

```yaml
bot:
  persona: |
    Warm, friendly, a little playful. You love food and treat every
    customer like a neighbor.
  guardrails:
    universal: |
      Only state facts explicitly listed in this config or in the
      retrieved business document excerpts. Never invent prices,
      flavors, ingredients, allergens, or policies you don't see here.
    sms: "Keep replies under 320 characters."
    voice: "Reply in 1-2 short sentences. No emojis, no markdown."

booking:
  noun: "order"          # Bob's Plumbing says "appointment"
  extra_questions:
    - key: "customization"
      prompt: "Any customization details? (e.g. 'Happy Birthday Carl')"
```

The same `extra_questions` block defines both what the state machine asks
*and* what the language model extracts from free-form messages.

## Running locally

```bash
git clone https://github.com/Casey2022/Multi-Business-AI-Chatbot.git
cd Multi-Business-AI-Chatbot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:

```
ANTHROPIC_API_KEY=your-key
VOYAGE_API_KEY=your-key
SECRET_KEY=generate-with-secrets.token_hex(32)
ADMIN_PASSWORD=choose-one
ALLOW_UNSIGNED_REQUESTS=true    # dev only — lets curl tests skip Twilio signature checks
```

Then:

```bash
python3 app.py
```

The app self-heals on boot: it creates tables, registers the demo businesses,
and ingests their documents if any of that is missing. Visit
`http://127.0.0.1:5000/demo/bobs_plumbing`.

## Project structure

```
app.py              Flask server, channel adapters, message orchestration
rules.py            Keyword matching engine
llm.py              Claude integration, prompt assembly, structured extraction
rag.py              Document ingestion and semantic retrieval
scheduler.py        Booking state machine
db.py               All SQLite access
config.py           YAML loading and placeholder substitution
phone_utils.py      E.164 normalization
admin/              Admin portal (Flask Blueprint)
config/             Per-business YAML
documents/          Per-business knowledge bases
notes/              Design decisions, debugging lessons, roadmap
```

## Engineering notes

Some decisions worth explaining:

**Configuration over code.** Business-specific content never appears in
Python. This is what makes onboarding a client a 20-minute task instead of a
development cycle.

**Grounded confirmation.** An early version of the bot, having been told about
the booking process so it could answer questions about it, started *performing*
bookings — collecting dates and confirming orders that were never saved. The
fix was two-part: an explicit capability boundary in the prompt ("you have no
access to the booking system"), and a structural rule that confirmations are
derived from successful database writes rather than from conversation.

**Retrieval that admits ignorance.** Vector search always returns *something*.
A distance threshold discards weak matches so the bot can say "I don't have
details on that" instead of assembling an answer from irrelevant chunks.

**Hosted embeddings.** Local ONNX inference worked fine in development and was
unusable on a small container. Moving embeddings to an API removed ~200 MB of
runtime memory and a large model download on every cold start — an example of
the deployment target dictating the design.

**Fork-safe initialization.** The vector database client is created lazily,
per process. Creating it at import time meant gunicorn's forked workers
inherited locks from a thread that didn't exist in them, deadlocking every
request that touched retrieval. Invisible locally; fatal in production.

Further detail in [`notes/debugging_lessons.md`](notes/debugging_lessons.md).

## Roadmap

- Calendar integration (Google Calendar first) — bookings the owner can see
  on their phone
- Customer self-service cancellation and rescheduling
- Voice channel via Twilio — the architecture already anticipates it
- Notice-period enforcement on bookings
- Marketing site with embedded demo and paid signup

Reasoning and decision triggers for each are in
[`notes/future_directions.md`](notes/future_directions.md).

## Status

Working end-to-end and deployed. Built as a portfolio project with an eye
toward real commercial use.
