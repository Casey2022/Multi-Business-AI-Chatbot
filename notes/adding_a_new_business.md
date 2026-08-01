# Adding a New Business — Quick Reference

Use this every time you onboard a new client. Five steps, no code changes required.

---

## Checklist

- [ ] 1. Create `config/<slug>.yaml`
- [ ] 2. Create `documents/<slug>/services.md`
- [ ] 3. Register the business in the database
- [ ] 4. Ingest RAG documents
- [ ] 5. Restart the server and test

---

## Step 1 — Create the config file

Create a new file at `config/<business_slug>.yaml`.

**Naming rule:** the slug must match the business name run through `_slugify()`:
- Lowercase everything
- Remove apostrophes (`'` → dropped)
- Replace `&` with `and`
- Replace accented characters (`é` → `e`)
- Replace spaces with underscores

Examples:
- `"Bob's Plumbing"` → `bobs_plumbing`
- `"Sunrise Bakery & Café"` → `sunrise_bakery_and_cafe`
- `"Maria's Hair Studio"` → `marias_hair_studio`

### YAML template

```yaml
# config/<slug>.yaml — <Business Name>

business:
  name: "<Business Name>"                 # Display name — can include punctuation
  phone: "(<area>) <prefix>-<line>"       # Shown to customers in replies
  address: "<street>, <city> <state>"
  hours: "<days>, <open>-<close>"
  service_area: "<description of coverage area>"

services:                                 # Used in booking prompts + system prompt
  - "<service 1>"
  - "<service 2>"
  - "<service 3>"

bot:
  persona: |
    <Describe the bot's voice in 2-3 sentences.>
    Examples: "Friendly and professional." / "Warm, playful, treats every
    customer like a neighbor." / "Direct and efficient."
  guardrails:
    universal: |
      Only state facts explicitly listed in this config or in the
      retrieved business document excerpts. Never invent prices,
      times, or policies you don't see here.
      If unsure, give the phone number and stop — do not speculate.
    sms: |
      Keep replies under 320 characters (SMS length).
      Plain text and a sparing emoji are fine.
    voice: |
      Reply in 1-2 short sentences (under 8 seconds spoken).
      No emojis, no markdown, no parentheticals or asides.

faq:                                      # Common questions with canned answers
  - question: "<question 1>"             # These get injected into the system prompt
    answer: "<answer 1>"
  - question: "<question 2>"
    answer: "<answer 2>"

rules:                                    # Keyword rules for instant replies
  - name: "greeting"
    keywords: ["hi", "hello", "hey", "yo"]
    match: "exact"
    reply: "Hi there! 👋 Welcome to {business_name}. How can I help?"

  - name: "hours"
    keywords: ["hours", "open", "closed", "close"]
    match: "any"
    reply: "We're open {hours}."

  - name: "location"
    keywords: ["where", "location", "address", "directions"]
    match: "any"
    reply: "We're at {address}."

  - name: "thanks"
    keywords: ["thanks", "thank you", "thx"]
    match: "any"
    reply: "You're welcome! 😊"

  # Add business-specific rules below.
  # match: "exact" → entire message must equal a keyword
  # match: "any"   → keyword appears anywhere in the message
  # Available placeholders: {business_name} {phone} {hours} {address}
  #                         {service_area} {services_examples}

booking:
  noun: "<appointment|order|booking|reservation>"   # What this business calls a booking
  noun_plural: "<appointments|orders|bookings|reservations>"
  greeting: "Happy to help you schedule a {noun}! What service do you need? (e.g. {services_examples})"
  service_confirmation: "Got it — {service}."
  ask_datetime: "When works for you? (e.g. 'Tuesday at 3pm' or 'next Friday morning')"
  final_confirmation: "Perfect! {noun} confirmed: {service} on {datetime}. We'll call to confirm. Reply 'cancel' anytime."
  fallback_after_bad_date: "Sorry, I couldn't read that as a date. Try something like 'Tuesday at 3pm' or 'June 5 at 10am'."
  cancel_reply: "No problem — {noun} cancelled. Text 'book' anytime to start again."
```

---

## Step 2 — Create the documents folder and services file

Create the folder: `documents/<slug>/`
Create the file:   `documents/<slug>/services.md`

**The slug must exactly match what you used in Step 1.**

### `services.md` template

```markdown
# <Business Name> — Services Guide

## <Service Category 1> (Topics: <query-likely keywords>)
<Describe the service. Include prices, timelines, what's included.
Write in complete sentences. The bot will retrieve this text to answer
customer questions, so phrase it the way customers would search for it.>

## <Service Category 2> (Topics: <query-likely keywords>)
<Description...>

## Service Area
<Where you operate, any travel fees, coverage limitations.>

## <Things You Don't Do> (Not Offered)
<List any common requests you cannot fulfil and what you'd recommend instead.
IMPORTANT: put the topic keywords IN THE HEADING, not just the body text.
Embeddings struggle with negation — "We don't do X" is hard to match.
"X (Not Offered)" in the heading is easy to match.>
```

### Document writing tips

- **Headings are retrieval anchors.** Put the topic keywords in the heading,
  not buried in the body. Customers search "do you do X" — the heading
  should contain "X".
- **Prices improve answers.** If the document has real prices, the bot gives
  real prices. If it doesn't, the bot has to say "call us."
- **"(Topics: ...)" annotations help.** Adding query-likely synonyms to a
  heading improves retrieval for customers who use different words.
- **Negation needs explicit headings.** "What We Don't Do" is nearly invisible
  to semantic search. "Septic Tanks and Well Drilling (Not Offered)" is not.
- **Aim for 300-500 characters per section.** Too short = not enough context.
  Too long = the chunk gets split mid-thought.

---

## Step 3 — Register in the database

### Option A: Add to `seed_businesses.py` (recommended)

Open `seed_businesses.py` and add a new entry to the `businesses` list:

```python
{
    "name":          "<Business Name>",
    "slug":          "<slug>",
    "twilio_number": "+1<10-digit-number>",  # E.164 format — must match Twilio exactly
    "config_path":   "config/<slug>.yaml",
},
```

Then run:

```bash
rm chatbot.db
python3 seed_businesses.py
```

### Option B: Add without resetting the database

If the server has live data you don't want to lose, run a one-liner instead
of resetting:

```bash
python3 -c "
from dotenv import load_dotenv; load_dotenv()
from db import init_db, add_business
init_db()
add_business(
    name='<Business Name>',
    slug='<slug>',
    config_path='config/<slug>.yaml',
    twilio_number='+1<10-digit-number>'
)
print('Done.')
"
```

### Twilio number format

The `twilio_number` stored in the database must be in **E.164 format**:
`+` followed by country code followed by 10 digits. No spaces, dashes,
or parentheses.

Examples:
- `+15855550123` ✓
- `(585) 555-0123` ✗
- `585-555-0123` ✗

This must match exactly what Twilio sends in the `To` field of the webhook.
Check your Twilio console to confirm the exact format.

---

## Step 4 — Ingest RAG documents

run:

```bash
python3 rag.py
```

Verify the output looks like:

```
[config] Loaded config for: <Business Name>
[rag] Using collection: <slug>
[rag] Ingesting documents from documents/<slug>...
[rag] services.md: N chunks
[rag] Ingested N total chunks into '<slug>'.
```

---

## Step 5 — Restart and test

```bash
# In terminal 1 — restart the server
python3 app.py
```

Then test in terminal 2 using the new business's Twilio number in the `To` field:

```bash
# Rules test
curl -X POST http://127.0.0.1:5000/sms \
  --data "Body=hi&From=%2B15550001111&To=%2B1<new-twilio-number>"

# RAG test (something only the document would know)
curl -X POST http://127.0.0.1:5000/sms \
  --data "Body=<service-specific question>&From=%2B15550001111&To=%2B1<new-twilio-number>"

# Booking test
curl -X POST http://127.0.0.1:5000/sms \
  --data "Body=book&From=%2B15550001111&To=%2B1<new-twilio-number>"
```

**Watch terminal 1 for:**
```
[config] Loaded config for: <Business Name>
[app] Serving: <Business Name> (id=N)
```

If you see the right business name, the routing worked.

---

## Common mistakes

| Symptom | Likely cause | Fix |
|---|---|---|
| 404 on every request | Twilio number not in database or wrong format | Check E.164 format; verify with `SELECT * FROM businesses;` |
| Bot answers as wrong business | Slug in DB doesn't match config/documents folder | All three must use the same slug |
| RAG returns 0 chunks for everything | Documents not ingested for this business | Run `python3 rag.py` with correct config path |
| RAG returns wrong business's chunks | Slug mismatch between collection name and config | Delete `chroma_db/` folder and re-ingest both businesses |
| Booking says "appointment" instead of business's noun | Missing or wrong `booking.noun` in YAML | Check the `booking:` block in the config |
| Config placeholder shows literally `{phone}` | Key name typo in YAML | Must be `phone:` not `Phone:` or `PHONE:` |

---

## Files touched when adding a business

| File | What changes |
|---|---|
| `config/<slug>.yaml` | **Created** — new business config |
| `documents/<slug>/services.md` | **Created** — business knowledge base |
| `seed_businesses.py` | **Updated** — add to businesses list (if using Option A) |
| `rag.py` (temporarily) | `__main__` config path changed for ingest run, then restored |
| `chatbot.db` | **Updated** automatically — new row in businesses table |
| `chroma_db/` | **Updated** automatically — new collection created by ingest |

**No Python source files need editing to add a new business.**
That's the point of the architecture.

---

## README section (copy this into README.md when ready)

```markdown
## Adding a New Business

This system is designed to serve multiple businesses from a single server.
Adding a new business requires no code changes — only configuration and content.

### Quick steps

1. **Create `config/<slug>.yaml`** — copy `config/bobs_plumbing.yaml` as a
   template and fill in the new business's details.

2. **Create `documents/<slug>/services.md`** — write the business's service
   information. This is what the bot uses to answer specific questions about
   prices, services, policies, and availability.

3. **Register the business:**
   ```bash
   python3 -c "
   from dotenv import load_dotenv; load_dotenv()
   from db import init_db, add_business
   init_db()
   add_business('<Name>', '<slug>', 'config/<slug>.yaml', '+1<number>')
   "
   ```

4. **Ingest documents** — update the config path in `rag.py`'s `__main__`
   block and run `python3 rag.py`.

5. **Restart the server** — `python3 app.py`.

See `notes/adding_a_new_business.md` for the full reference guide including
templates, troubleshooting, and document writing tips.
```