# Debugging Lessons — Patterns That Took Time to Figure Out

Things I've hit while building, with the diagnosis and fix. Add to this
every time something takes more than 5 minutes to debug — future-me will
hit the same thing again.

## How to read Python tracebacks

**Bottom-up, not top-down.** Tracebacks list the call chain from the
outermost call to the deepest one. The noise tells you *where* it failed;
the last line tells you *what* failed. Always read the last line first.

The error type itself is the headline:
- `NameError: name 'X' is not defined` → typo, or X is defined below
  where it's used, or X isn't defined at all
- `OperationalError: no such table: X` → database file is missing or stale
  (see "Reset DB → restart server" below)
- `sqlite3.IntegrityError` → constraint violation, usually a duplicate key
- `KeyError: 'X'` → dict lookup with a key that doesn't exist

## Reset DB → restart server

These two actions go together. **Always.**

When you delete `chatbot.db`:
- The file vanishes, but the running server doesn't know.
- `init_db()` only runs at startup, so the new empty file never gets tables.
- First subsequent request crashes with "no such table".

Fix: after every `rm chatbot.db`, **restart the server** (Ctrl+C → `python3 app.py`).
Say it as one action in your head: "delete and restart."

## "My curl returns nothing" / "The server isn't responding"

curl prints nothing because **nothing is listening on the port**. The
server either crashed or was never started. Glance at terminal 1:
- Is it still showing `* Running on http://127.0.0.1:5000` and `Press CTRL+C to quit`?
- Or did a Python traceback fire and the prompt come back?

If the server died with a traceback, **debug.py auto-reload doesn't help.**
Auto-reload recovers from crash-on-request, not crash-on-startup. Fix the
error and restart manually.

The habit: before debugging the request, confirm the thing receiving it is alive.

## Two-terminal discipline

- **Terminal 1**: running Flask server. *Occupied.* Anything typed here
  goes to the running program, not the shell.
- **Terminal 2**: free. For curl, pip install, rm, python3 db.py, etc.

If you forget which is which: the prompt of an active terminal looks
like `(venv) caseycaudle@... %`. A terminal running Flask shows ongoing
log lines and no prompt — that's the "occupied" signal.

## NameError almost always means one of three things

1. Typo in the name
2. The definition appears *below* where it's used (Python reads top-to-bottom)
3. The name is missing entirely

The error message tells you the use site (e.g. "line 15"). Look *upward*
from there for the definition.

Hit during Stage 4: had `BOOK_INTENT` referenced in `RULES = [...]` before
`BOOK_INTENT = "__BOOK__"` was defined above it. Reordering fixed it.

## `pip install` fails with "Failed to resolve"

Read past the wall of red. The keyword is the verb:
- "Failed to resolve" → DNS problem (your computer can't find the server)
- "Connection refused" → server unreachable
- "Timed out" → server slow or unreachable
- "No matching distribution found" → real packaging issue

Networking errors are almost never a Python problem. Check Wi-Fi,
toggle it off/on, try a browser, then retry. `pip` is stateless — failed
attempts didn't break anything.

## URL-encoded form data: `+` decodes as space

In `Body=+15551234567&...`, the `+` becomes a space when Flask decodes it,
because in URL form encoding, `+` means space.

- For curl tests, use `%2B` instead: `From=%2B15551234567`
- For real Twilio webhooks, this isn't an issue — Twilio encodes correctly.
- For production safety, normalize phone numbers before using them as keys.

## VS Code popup: "environment file is configured but not injected"

Harmless. Dismiss it. Your Python code loads `.env` itself via
`python-dotenv`; you're not relying on VS Code to inject it.

## SQLite database file shows binary garbage in VS Code

That's correct — `.db` files are binary, not text. VS Code refuses to
show garbled bytes. To actually look inside:
- Read via your own code (e.g., `get_recent_messages()`)
- Install a "SQLite Viewer" extension to see tables as spreadsheets
- Run `sqlite3 chatbot.db` for an interactive prompt

## Don't put `python3 -c "..."` one-liners in Mac zsh

Mixed quote escaping between shell and Python is a nightmare. If it
would be more than one line of Python, write it as a real script. Cheaper
to debug a real script than a misquoted shell command.

## When LLM output is "weird" or wrong

Read the system prompt first. Almost every "the LLM is confused" issue
traces back to one of:

1. **Missing fact in the prompt.** The model only knows what's in its
   context. It hallucinates plausibly when there's a gap. Example: bot
   asked the user for the business's phone number because the prompt
   never gave Claude a phone number.
2. **Too much speculation room.** Add explicit "do not speculate"
   guardrails. Example: "If unsure, give the phone number and stop."
3. **Format drift.** For structured extraction, include few-shot
   examples — show the exact output shape, don't just describe it.

Don't reach for "tune the temperature" or "switch models" until you've
fixed the prompt.

## When RAG returns the wrong chunks

In order of likelihood:
1. **Chunks are malformed** — split mid-sentence, mid-word, or contain
   multiple unrelated topics. Run a chunk inspector to look directly.
2. **Document phrasing buries the topic** — headings should contain
   query-likely keywords. "What We Don't Do" is invisible to the embedder
   for "do you do X" queries; "X, Y, Z (Not Offered)" works.
3. **Query is too short/vague** — distances cluster near 0.7+, no
   confident top match. This is a query problem, not a document problem.
4. **Distance threshold is too loose** — chunks above ~0.85 are usually
   noise. Tighten if irrelevant content keeps sneaking through.

## The "eager rule" problem

Keyword rules are dumb — they don't understand context. A rule with
keyword "how much" will steal "how much notice do you need" away from
the smarter LLM/RAG path.

Fixes, in order:
1. **Narrow the keyword list** — drop ambiguous ones like "how much"
2. **Remove the rule entirely** — if RAG/LLM can now answer better,
   the rule is outliving its purpose
3. **Check before firing** — have the rule defer to LLM if confidence
   is high there (more complex; only if needed)

Rule-based systems require ongoing tuning against actual usage. That's
not a flaw of the design; it's the cost of using rules.

## Order of operations with stored data

Pattern: "fetch history → save new message" vs. "save new message →
fetch history".

If you save first, the just-saved message appears in the history, then
gets passed to the LLM, which also receives it as the current message —
duplicated. Fix: fetch history *before* saving the new message.

Order-of-operations bugs with persistent data are silent (no crash, just
wrong output). Worth being deliberate about every time.

## When I'm tempted to "add a feature" to fix a bug

Stop. Look at what changed since it last worked. The bug is almost
always in the recent change, not somewhere needing more code.

## When using a strict library, separate its strict mode from your loose use case

Hit this with `phonenumbers.is_valid_number()` — the library considers
fictional area codes like 555 to be invalid (correctly, for production
validation). But my normalization function used it as a gate, which
meant test phone numbers came back unchanged.

Lesson: **normalization and validation are separate concerns.** A
formatter should reformat anything it can parse. A validator can be
called later, separately, when validity actually matters.

Diagnostic move that exposed it fast: ran the utility in isolation
with `python3 -c "from phone_utils import normalize; print(normalize('(555) 111-2222'))"`
— skipped the whole Flask/curl/server stack, saw the raw function
output. Confirmed it was the function, not the wiring.

## Re-ingesting RAG documents requires a server restart

If the Flask server is running when I run `python3 rag.py`, the server
caches a UUID handle to the OLD collection. The ingest script deletes
that collection and creates a new one with a new UUID. Next request
crashes with `chromadb.errors.NotFoundError: Collection [<uuid>] does
not exist.`

Pattern: any time external state changes (`rm chatbot.db`,
`python3 rag.py`, manual ChromaDB edits), restart the server so it
picks up fresh handles. Debug-mode auto-reload doesn't catch this —
it watches code, not data.

## LLM prompts have no clock — and few-shot examples leak context

parse_datetime booked "Tuesday at 2pm" a month in the past. Cause: the
prompt never stated today's date, and the hardcoded example dates were
written in June. The model inferred "today" from the examples — the only
date signal available — and answered correctly *for June*.

Lessons:
1. Any time-relative LLM task must state the current date in the prompt.
2. Few-shot examples teach more than format — they imply context
   (dates, places, assumptions) that the model treats as true.
3. Compute example dates dynamically so they can't go stale. Fix the
   class of bug, not the instance.
4. Plausible-looking output isn't verified output. The bug hid for a
   month because timestamps *looked* reasonable and we never checked
   the actual day-of-week arithmetic.


## 403 at 127.0.0.1:5000 on Mac = Flask probably isn't running

macOS AirPlay Receiver also listens on port 5000 and returns HTTP 403.
If Flask crashes (e.g. syntax error killed the reloader), the browser
still reaches AirPlay and shows "Access denied / HTTP ERROR 403" instead
of connection-refused. Check terminal 1, restart the server. Permanent
fixes: disable AirPlay Receiver, or use a different port.

## RAG hallucinations can be real facts attached to the wrong subject

The bot told a customer cupcake orders need "48 hours' notice." That
number exists in our documents — but for pastry trays, not cupcakes.
The cupcake section said nothing about notice at all. Retrieval pulled
both chunks (they're semantically adjacent), and the model blended a
fact from one service into a claim about another.

This is the hardest hallucination type to catch: every ingredient is
true, only the attribution is wrong. It passes a lazy read because the
number "looks right."

Diagnosis pattern: when the bot states a specific fact, check WHICH
chunk it came from (the [rag] logs show all retrieved chunks and
distances). If the fact lives in a different section than the subject
of the answer, it's cross-service misattribution.

Fixes, in order of effectiveness:
1. **Make the document explicit where it's silent.** A section that
   doesn't state its policy invites the model to borrow one from a
   neighboring chunk. Silence in sources becomes confabulation in output.
2. **Name the failure mode in the guardrails.** Added: "Never apply a
   fact from one service to a different service — notice periods,
   prices, and policies are service-specific."

Meta-lesson: this was caught by reading a transcript critically, not by
any test failing. LLM products need a human QA habit of checking claims
against sources — plausible output is not verified output (same lesson
as the date-parsing bug, different disguise).


## When the same manual step gets forgotten twice, automate it. The hardcoded ingest path in rag.py's main caused two stale-collection bugs before we made it read the businesses table instead.


## Describing a capability to an LLM invites it to simulate that capability. 

The booking_text that taught Claude our flow led it to perform the flow — collecting details and confirming an order that was never saved. Knowledge of a process must be paired with an explicit 'you cannot execute this' boundary. Corollary: confirmations must be grounded — only claim an action happened when the database write succeeded.

## LLM prompts have no clock — audit every prompt, not just the one that failed

parse_datetime booked "Tuesday at 2pm" a month in the past. The prompt never stated today's date, and the hardcoded few-shot example dates had been written in June. The model inferred "today" from the examples — the only date signal available — and answered correctly for June.

Fixed it there. Weeks later the same bug appeared in conversation: the bot said "tomorrow's Monday" on a Wednesday, because build_system_prompt had never been given a clock either.

Lessons:

Any time-relative LLM task must state the current date in the prompt.
Few-shot examples teach more than format — they imply context (dates, places, assumptions) the model treats as true.
Compute example dates dynamically so they can't go stale. Fix the class of bug, not the instance.
When you find a bug of this kind, grep for every other place the same condition could exist. We fixed one prompt and assumed we were done.
Plausible output is not verified output. This hid for a month because the timestamps looked reasonable and nobody checked day-of-week arithmetic.

## RAG hallucinations can be real facts attached to the wrong subject

The bot told a customer cupcake orders need "48 hours' notice." That number exists in our documents — but for pastry trays, not cupcakes. The cupcake section said nothing about notice at all. Retrieval pulled both chunks (they're semantically adjacent) and the model blended a fact from one service into a claim about another.

Hardest hallucination type to catch: every ingredient is true, only the attribution is wrong. It survives a lazy read because the number looks right.

Diagnosis pattern: when the bot states a specific fact, check WHICH chunk it came from — the [rag] logs show every retrieved chunk. If the fact lives in a different section than the subject of the answer, it's cross-service misattribution.

Fixes, in order of effectiveness:

Make the document explicit where it's silent. A section that doesn't state its policy invites the model to borrow one from a neighbouring chunk. Silence in sources becomes confabulation in output.
Name the failure mode in the guardrails: "Never apply a fact from one service to a different service — notice periods, prices, and policies are service-specific."

## Describing a capability to an LLM invites it to simulate that capability

We added booking_text to the system prompt so Claude could answer questions about the booking process accurately. It worked — and then it went further. Given a customer who described what they wanted, Claude ran the entire booking itself: asked for the date, the time, the name, mimicked our extra questions, and closed with "You're all set — Wednesday August 6th by 9am."

Nothing was saved. The state machine never engaged. Every turn logged no match -> defer to LLM. A customer would have shown up to a bakery that had never heard of them.

We gave the model the script and it performed the play. The guardrail said "tell customers we'll ask for details during booking" — nothing ever said you are not the one who takes bookings.

Lessons:

Knowledge of a process must be paired with an explicit boundary about who executes it. Capability-shaped denials: "you have no access to X", "do NOT collect Y yourself", "NEVER state or imply that Z has happened".
Grounded confirmation: only claim an action happened when the system of record says it happened. A confirmation message must be derived from the database write, never from the conversation. If there was no write, there is no confirmation to give.
Related earlier incident, same disease: a customer typed "cancel" after their order had already been placed. Not mid-booking, so it fell to the LLM, which cheerfully replied "Got it, order cancelled." Nothing was cancelled.

## When a filter doesn't filter, measure the data — don't guess the threshold

Added a filter to drop chunks under 40 characters to remove an orphan document title. The orphan stayed: the title was 46 characters.

Lesson: when setting a threshold, measure the thing you want to exclude and the thing you want to keep. The boundary case is the only one that matters. Better still: prefer a structural test ("does this chunk have content beyond a heading?") over a magic number when the structure itself reveals the property.

## Normalization and validation are separate concerns

phone_utils.normalize() returned input unchanged for (555) 111-2222. Cause: it used phonenumbers.is_valid_number() as a gate, and 555 area codes are reserved for fiction — correctly invalid, and irrelevant to formatting.

Lesson: a formatter should reformat anything it can parse. A validator answers a different question and belongs in a different function. Strict libraries are often right for their purpose and wrong for yours.

Diagnostic move that cracked it fast: run the utility in isolation (python3 -c "from phone_utils import normalize; print(normalize('(555) 111-2222'))") — skipping the entire Flask/curl/server stack proved the function, not the wiring, was at fault.

## When the same manual step gets forgotten twice, automate it

rag.py's __main__ block had a hardcoded config path. Ingesting a second business meant hand-editing that line, running, then editing it back. This caused two stale-collection bugs where a document edit appeared saved but never reached ChromaDB.

Fixed by having the script read config_path from the businesses table and ingest every active business by default, with an optional command-line argument for single-business runs.

Lesson: a manual step you've forgotten twice isn't a discipline problem, it's a design problem. The fix also removed a source-of-truth drift — the ingest targets now come from the same table the server uses.

## Re-ingesting RAG documents requires a server restart

If Flask is running when python3 rag.py runs, the server may hold a stale collection handle. The ingest script deletes and recreates the collection with a new UUID; the next request crashes with chromadb.errors.NotFoundError: Collection [<uuid>] does not exist.

General rule: when you modify shared state from outside the running server, restart the server. Debug-mode auto-reload watches code, not data. Same family as "delete chatbot.db → restart" (the tables only get created by init_db() at startup).

## SyntaxError on valid-looking code = you pasted it inside an f-string

booking = config.get("booking", {}) threw SyntaxError: f-string: valid expression required before '}'.

The line is valid Python. The giveaway is the phrase "f-string" in the error: the code had been pasted inside a triple-quoted f-string, so Python was reading it as template text and choked on {} as an empty placeholder.

Mental model: everything before return is the kitchen (where variables get cooked); the f-string is the plate (where finished variables get arranged). Code goes in the kitchen; only {variable_name} goes on the plate.

## 403 at 127.0.0.1:5000 on a Mac usually means Flask isn't running

macOS AirPlay Receiver also listens on port 5000 and answers with HTTP 403. When Flask dies (e.g. a syntax error killed the reloader), the browser still reaches something and shows "Access denied / HTTP ERROR 403" instead of the connection-refused you'd expect from a dead server.

Check terminal 1 first. Permanent fixes: disable AirPlay Receiver in System Settings, or run Flask on another port.

## Multi-line curl commands are fragile when pasted

Curls written with \ line continuations got mangled by the shell during paste — the commands appeared to run but never reached the server, and an entire round of "testing" was actually testing nothing. Confirmed by querying the messages table directly and finding no rows from that session.

Default to single-line curls for anything you'll copy-paste. And when test results look ambiguous, check the database rather than trusting the terminal.

## Fork-unsafe clients: the bug that produced no error at all

Symptom. Every request that touched RAG hung until gunicorn killed the worker: [CRITICAL] WORKER TIMEOUT followed by Worker (pid:X) was sent SIGKILL! Perhaps out of memory?. No traceback. No error. Worked perfectly on localhost. Raising the timeout to 300s changed nothing — it just took longer to die.

Cause. rag.py created the ChromaDB client at module import:

python
_chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))

Gunicorn forks worker processes. ChromaDB is Rust-backed and holds internal locks and file handles; a client created before the fork gets copied into the child, where the thread holding those locks does not exist. The child waits on a lock nobody will ever release. Deadlock.

Fix. Create the client lazily, so it is born inside whichever process actually uses it:

python
_chroma_client = None

def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    return _chroma_client

The generalisable rule. Anything holding OS-level resources — database connections, HTTP session pools, thread pools, native/Rust/C extension state — should be created per process, lazily, not at module import. Local dev servers are single-process, so this class of bug is invisible until you deploy behind a forking server.

Misleading detail worth remembering: gunicorn's "Perhaps out of memory?" in the SIGKILL message is a guess, not a diagnosis. It sent us chasing the 512 MB limit for two rounds. Gunicorn prints that whenever a worker dies without explanation.

When something hangs with no error, bisect it with print statements

We spent rounds guessing (CPU limits? memory? rate limits?) and got nowhere. What actually cracked it was adding logging between every step of the suspect function:

python
print(f"[rag] Opening collection '{collection_name}'...")
collection = get_chroma_client().get_collection(...)
print("[rag] Collection opened. Embedding query and searching...")
results = collection.query(...)
print(f"[rag] Search returned {len(results['documents'][0])} raw results.")

The log stopped after "Opening collection" and never reached "Collection opened." That single fact eliminated CPU, embeddings, rate limits, and the LLM in one shot — the hang was in a call that should take milliseconds.

Rule: a hang gives you no stack trace, so manufacture one. Add prints until the gap between the last line printed and the next line expected is a single call. That call is your bug.

Prerequisite: set PYTHONUNBUFFERED=1 in any containerised Python environment. Without it Python buffers stdout when not attached to a terminal, and logs from a killed worker are lost entirely — which is why early attempts showed nothing at all.

The deployment environment can make a correct design untenable

The app ran fine locally with ChromaDB's default embedder (a local ONNX model). On a 0.1-CPU container it was hopeless: neural-net inference on a tenth of a core, plus a 79 MB model download on every cold start, on an ephemeral filesystem that discarded the cache each time.

Fix: move embeddings to a hosted API (Voyage AI). Query embedding became a fast network call, ~200 MB of runtime memory disappeared, and cold starts dropped substantially.

The lesson isn't "APIs are better." Local embedding is the right call in plenty of contexts — no per-query cost, no network dependency, full privacy. It was wrong for this deployment target. Adapting to the constraints of the environment you're shipping into, rather than fighting them, is most of what deployment work actually is.

Migration gotcha: different embedding models produce different vector dimensions, so existing collections become unreadable. All collections must be rebuilt after a swap. Also: the embedding function must be passed when reading a collection, not just when creating it — miss it and Chroma silently falls back to its default embedder, producing query vectors that can't be compared to what's stored.

Partial ingestion that reports success

Symptom. Bot gave plausible answers, but ensure_ingested logged "Collection 'bobs_plumbing' already has 3 chunks — skipping" when the document produces 7. Less than half the knowledge base was loaded, and nothing complained.

Three failures lined up:

ingest_documents called collection.add() once per chunk — 7 chunks meant 7 API calls, and Voyage's unpaid free tier caps at 3 requests per minute. The fourth was rejected.
bootstrap() caught the exception, logged a warning, and continued.
ensure_ingested checked count > 0, so a partial collection looked healthy forever afterwards.

Fixes:

Batch the writes: one collection.add() per file, not per chunk.
Check completeness, not existence — compare the stored count against the chunk count the source documents would produce, and rebuild on mismatch.

Rule: a health check that asks "is there anything here?" will eventually bless corrupt data. Ask "is there everything that should be here?"

## Platform-specific deployment gotchas (Render)
Bind to $PORT. Render assigns a port via environment variable and scans for a listener. Gunicorn defaults to 8000 → deploy fails with "no open ports detected." Use --bind 0.0.0.0:$PORT.
runtime.txt is ignored. Render reads a PYTHON_VERSION environment variable instead. Without it we silently got Python 3.14 while developing against 3.13 — dangerous with compiled extensions like ChromaDB and ONNX.
The filesystem is ephemeral. Anything written at runtime vanishes on restart or redeploy. This is why bootstrap() exists: the app must be able to rebuild its database and vector store from nothing on every boot.
Free tier spins down after 15 minutes idle, ~30-60s to wake (longer if boot re-downloads or re-ingests anything). Fine for a casual link; wake it manually before a live demo, or keep it warm with an uptime pinger.
Procfiles are a Heroku convention. Render uses the Build Command and Start Command fields in its UI.
pip freeze captures your whole environment, not your dependencies

requirements.txt picked up langchain-core, langsmith, pillow, uvicorn, uvloop, watchfiles and others — none of which this project imports. They came from unrelated experiments in the same venv, and they bloated every build and cold start.

Rule: pip freeze is a snapshot of the environment, not a declaration of intent. For anything you deploy, write requirements.txt by hand with the packages you actually import, or use a tool that tracks direct dependencies separately from transitive ones.

## Meta-lesson: know when the environment is the problem

Six rounds of debugging, and the recurring temptation was to assume the free tier's limits (0.1 CPU, 512 MB) were the cause. They were plausible, they were real constraints, and they were not the bug. Two of them (local embeddings, ephemeral filesystem) genuinely required design changes; the actual blocker was a one-line fork-safety issue that would have occurred on any tier.

Worth separating, when deploying: "this environment is too small" versus "my code assumes a single process." The first is a spending decision. The second is a bug, and no amount of money fixes it.

## Prove an integration in isolation before wiring it in
Before touching scheduler.py, the calendar write was tested with a standalone one-liner that loaded the config and called create_event() directly. Two failures surfaced there — a wrong file path and a malformed JSON credential — and each took about two minutes to fix.

Had those been discovered through the booking flow instead, they would have arrived as a Flask traceback, several conversational turns deep, mixed in with RAG and LLM output.

Rule: when adding a dependency on an external service, get it working in the smallest possible script first. The integration and the wiring are two separate problems; debugging them simultaneously is much harder than debugging them in sequence.

## Reading JSON decode errors precisely
JSONDecodeError: Extra data: line 1 column 2340 (char 2339) — the service account credential wouldn't parse.

"Extra data" specifically means valid JSON followed by junk, and the column number is where the valid part ended. Slicing the string around that index showed a single extra } — one stray character from an overshot selection when copying a 2,000-character value.

Distinguish the messages:

"Extra data" — valid JSON, then trailing garbage
"Expecting value" — malformed or empty from the start
"Unterminated string" — truncated, often a newline in the middle

A diagnostic worth reusing for any long environment variable:

raw = os.environ.get('SOME_JSON', '')

print('Length:', len(raw))

print('Tail:', repr(raw[-40:]))

## Multi-line secrets in environment variables
A service account key is a multi-line JSON file, and environment variables are single-line. Collapse it first:

python3 -c "import json; print(json.dumps(json.load(open('key.json'))))"

Then paste the result as one line in .env. Turn off editor word wrap while doing this — soft wrapping looks identical to a real newline, and a real newline breaks the value silently.

Delete the downloaded key file afterwards. A credential sitting on the Desktop eventually ends up in a screenshot or a backup.

## Fork-safety applies to every client
calendar_sync.py builds its Google API client lazily via _get_service(), for the same reason rag.py builds the ChromaDB client lazily: gunicorn forks workers, and clients created at import time are inherited across the fork along with whatever locks and connections they hold.

Having paid for this lesson once with an errorless deadlock, the pattern is now applied by default to any new client object. A lesson learned is only worth what you apply it to next.

## Test the failure path deliberately
The success path was verified by watching an event appear in Google Calendar. The failure path was verified by corrupting the calendar ID, attempting a booking, and confirming the appointments table was unchanged.

The second test is the one that proves the design. It's also the one that's easy to skip, because "nothing happened" doesn't feel like a result.

Method: query the relevant table before and after, and compare. Log lines alone aren't sufficient — the absence of a [scheduler] Saved line suggests nothing was written, but the database is the actual source of truth.

## `.replace(tzinfo=None)` discards a timezone; `.astimezone(tz)` converts to one

**Symptom.** Availability checking ran without error and cheerfully allowed
two bookings at the same time. No exception, no warning — just wrong answers
that looked right.

**Cause.** Google's freeBusy API returns RFC3339 timestamps with an explicit
offset: a 2pm Eastern event comes back as `2026-08-25T18:00:00Z`. The parsing
code did:

```python
start = datetime.fromisoformat(b["start"]).replace(tzinfo=None)
```

`.replace(tzinfo=None)` **strips** the timezone without adjusting the clock
time — so 18:00 UTC became a naive 18:00, and comparing it against a naive
local 14:00 found no overlap. Every conflict check was silently off by the
UTC offset.

**Fix.** Convert first, then strip:

```python
start = datetime.fromisoformat(b["start"]).astimezone(tz).replace(tzinfo=None)
```

`.astimezone(tz)` recalculates the clock time for the target zone. Stripping
tzinfo afterward is fine — by then the number is correct for the zone the
rest of the app works in.

**The general rule.** Any time an external API returns aware datetimes and
your application works in naive ones, the boundary between them is a bug
waiting to happen. Pick one convention (this project uses naive local time
internally) and convert explicitly at the edge — the same "normalize at the
boundary" principle already applied to phone numbers.

**Diagnostic that found it in one step:** print the parsed busy periods
directly rather than trusting the availability result.

```python
busy = _busy_periods(config, datetime(2026,8,25), datetime(2026,8,26))
for s, e in busy:
    print(s, '->', e)
```

Seeing `18:00 -> 19:00` for an event booked at 2pm made the four-hour shift
obvious immediately. **When a boolean comes back wrong, print the values it
was computed from.**

**Related trap in the same function:** the query window was built with
`.isoformat() + "Z"`, which asserts naive local datetimes are UTC — the
mirror image of the same mistake. Fixed by attaching the real zone with
`.replace(tzinfo=tz)` before serializing.

.replace(tzinfo=None) discards a timezone; .astimezone(tz) converts to one

Symptom. Availability checking ran without error and cheerfully allowed two bookings at the same time. No exception, no warning — just a wrong answer that looked right.

Cause. Google's calendar API returns RFC3339 timestamps with an explicit offset: an event booked at 2pm Eastern comes back as 2026-08-25T18:00:00Z. The parsing code did:

python
start = datetime.fromisoformat(b["start"]).replace(tzinfo=None)

.replace(tzinfo=None) strips the timezone without adjusting the clock time. So 18:00 UTC became a naive 18:00, was compared against a naive local 14:00, and found no overlap. Every conflict check was silently four hours off.

Fix. Convert first, then strip:

python
start = datetime.fromisoformat(b["start"]).astimezone(tz).replace(tzinfo=None)

.astimezone(tz) recalculates the clock time for the target zone. Dropping tzinfo afterwards is fine — by then the number is correct for the zone the rest of the application works in.

The mirror-image mistake was in the same function: the query window was built with .isoformat() + "Z", which asserts that naive local datetimes are UTC. Fixed by attaching the real zone with .replace(tzinfo=tz) before serializing.

General rule. When an external API returns timezone-aware datetimes and the application works in naive ones, that boundary is a bug waiting to happen. Pick one internal convention and convert explicitly at the edge — the same "normalize at the boundary" principle already applied to phone numbers.

Diagnostic that found it in one step: print the parsed values rather than trusting the boolean computed from them.

python
busy = _busy_periods(config, datetime(2026,8,25), datetime(2026,8,26))
for s, e in busy:
    print(s, '->', e)

Seeing 18:00 -> 19:00 for an event booked at 2pm made the four-hour shift immediately obvious. When a boolean comes back wrong, print the values it was computed from.

Sorting before truncating silently undoes the work upstream

Symptom. find_alternatives was rewritten to search forward and backward independently and interleave the results, so a customer would see options on both sides of the time they asked for. After the rewrite it returned three consecutive slots, all earlier — the exact behaviour the rewrite existed to fix. A separate diagnostic proved the forward search did find an available slot the next morning.

Cause. The final two lines:

python
return sorted(set(mixed))[:wanted]

The interleave produced [Wed 09:00, Tue 11:00, Tue 10:30, Tue 10:00] — correct, alternating sides. Then sorted() reordered it chronologically, putting all three Tuesday slots first, and [:3] kept those and discarded Wednesday. Sorting before truncating threw away everything the interleave had arranged.

Fix. Truncate first, sort only for display:

python
seen = []
for iso in mixed:
    if iso not in seen:
        seen.append(iso)
    if len(seen) >= wanted:
        break
return sorted(seen)

The list-based dedupe matters too: set() would have scrambled the interleave order before truncation even happened.

The interesting part. Every individual component was correct. The forward search worked. The backward search worked. The interleave worked. The sort worked. The truncation worked. The composition was wrong, and only the end-to-end output revealed it — unit tests on each piece would all have passed.

Rule: when a function chains transformations, the order of the last two operations is worth deliberate attention. Truncation destroys information; anything that decides which items matter must run before it.

API scope errors name the endpoint that failed

Symptom.

403 ... /calendar/v3/freeBusy ... "Request had insufficient authentication scopes."

Writing events worked fine; only availability checking failed.

Cause. The service account was authorized with https://www.googleapis.com/auth/calendar.events — enough to create and modify events, but not to query free/busy across a calendar, which Google treats as a broader read.

Fix. Request both scopes:

python
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

Rule. A 403 on one endpoint while others succeed is a scope problem, not a credentials problem. The URL in the error names exactly which capability is missing. Scope requirements are per-operation and rarely match intuition — "I can write events" does not imply "I can read the calendar."

Know what an API actually returns before designing around it

Symptom. Two events booked at the same time showed up as a single busy period. Fine for the exclusive scheduling model, fatal for the capacity model, which needs to count how many bookings occupy a slot.

Cause. freebusy().query() answers "is this calendar busy?" — it merges overlapping intervals into contiguous blocks. It reports availability, not events. There is no way to recover a count from it.

Fix. Switch to events().list(), which returns individual events. Three details that matter with it:

singleEvents=True expands recurring events into instances; without it a weekly appointment returns as one entry with a recurrence rule
Cancelled events remain in the response with status == 'cancelled' and must be filtered, or they falsely consume capacity
All-day events have a date rather than a dateTime and need skipping (open question: an all-day "VACATION" event arguably should block bookings — currently it doesn't)

Rule. Two API endpoints that look interchangeable often answer subtly different questions. This surfaced only because the diagnostic printed the raw periods — a summary count would have shown "1 busy period" and looked correct.

Meta-note

Three of these four bugs produced no error. The availability check returned True when it should have returned False; the alternatives function returned three valid slots that were merely the wrong three. Each was found by printing intermediate values rather than by anything failing.

That is the recurring shape of bugs in this project — dates that looked plausible, retrieval that returned reasonable-but-wrong chunks, a language model confirming bookings that were never saved. The habit that catches them is checking outputs against what they should be, not merely against whether an error appeared.

# An ambiguous prompt produces inconsistent answers, not consistent wrong ones

Symptom. "Wednesday at 7" was booked successfully. The identical phrase, minutes later, was rejected as outside business hours.

Cause. parse_datetime had no idea what hours the business kept. "7" with no am/pm is genuinely ambiguous, and the model resolved it as 07:00 on one call and 19:00 on the next. Both are defensible readings; nothing in the prompt preferred either. The bakery closes at 15:00, so the 19:00 reading was rejected — but for a reason that had nothing to do with what the customer meant.

Fix. Give the parser the business's operating window and tell it to prefer the interpretation that fits:

This business operates between 07:00 and 15:00. When a time is ambiguous
(e.g. "7" or "3" with no am/pm), choose the interpretation that falls
within those hours.

The lesson. Non-determinism in a model's output is often a symptom of under-specification in the prompt, not randomness for its own sake. When the same input produces different answers, the first question is: does the prompt contain enough information to determine one answer? Here it didn't, and the model was effectively guessing.

Note the diagnosis required comparing two runs. A single wrong answer looks like a bad model; two different answers to the same input point straight at ambiguity.

# A constraint with no satisfiable answer makes the model hedge out loud

Symptom. Shortly after adding the business-hours hint:

[llm] Date parse result: '2026-08-20 08:00\n\nWait, let me reconsider.'
[llm] Date parse error: unconverted data remains

The model began answering, then started second-guessing itself in the output.

Cause. The new hint said "choose the interpretation that falls within business hours." For "Thursday at 8" against Bob's 09:00–17:00, neither 08:00 nor 20:00 satisfies that. The instruction had no valid answer, and the model visibly wavered instead of committing.

Fix, two parts. Give the constraint an escape hatch:

If neither interpretation falls within business hours, pick the more likely
everyday interpretation and return it anyway — do not refuse, and do not
explain your reasoning.

And stop trusting the output to be clean:

python
match = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", result)
if not match:
    return None
result = match.group(0)

The lesson. Every constraint in a prompt needs a defined behaviour for the case where it can't be met, or the model will invent one — usually by talking. And any structured output should be extracted rather than assumed: the prompt discourages commentary, the regex survives it anyway.

Worth noting the validation caught this. strptime rejected the trailing text, parse_datetime returned None, and the customer got the normal "I couldn't read that as a date" fallback rather than a garbage booking. Poor experience, but no bad data — which is what validation is for.

# Sorting before truncating silently discards the work upstream

Symptom. find_alternatives was rewritten to search forward and backward and interleave the results, so customers would see options on both sides of the time they requested. After the rewrite it returned three consecutive earlier slots — the exact behaviour the rewrite existed to fix. A separate diagnostic proved the forward search did find an available slot.

Cause. The final line:

python
return sorted(set(mixed))[:wanted]

The interleave produced [Wed 09:00, Tue 11:00, Tue 10:30, Tue 10:00] — correctly alternating. Then sorted() reordered chronologically, putting all three Tuesday slots first, and [:3] kept those and dropped Wednesday.

Fix. Truncate first, sort only for display:

python
seen = []
for iso in mixed:
    if iso not in seen:
        seen.append(iso)
    if len(seen) >= wanted:
        break
return sorted(seen)

The list-based dedupe matters too — set() would have scrambled the order before truncation even happened.

The lesson. Every component was correct: the searches, the interleave, the sort, the truncation. The composition was wrong. Unit tests on each piece would all have passed.

Rule: when a function chains transformations, the last two operations deserve deliberate attention. Truncation destroys information, so anything that decides which items matter must run before it.

# Guards need to exist everywhere the condition matters

Symptom. On a Thursday afternoon, "Thursday at 8" parsed to 08:00 that morning — a time eight hours in the past. It was rejected, but only because 8am is before the business opens. "Thursday at 2" would have produced a past time inside business hours, and nothing would have caught it.

Cause. find_alternatives already skipped past candidates:

python
if candidate < datetime.now():
    continue

But is_slot_available and slot_rejection_reason — the functions that validate a directly requested time — had no such check. The guard existed in one place and not the two others that needed it.

Fix. Add the same check to both, plus a "past" rejection reason so the customer gets an accurate explanation rather than a misleading one. Also instruct the parser to always resolve to a future date, since a bare weekday name almost always means the next occurrence.

The lesson. When you write a guard, ask which other code paths reach the same decision. Here three functions answered variations of "is this slot usable" and only one knew about the past. The version that got the guard was the one where the bug happened to be noticed first.

Related: this is the same shape as the earlier fork-safety fix, where a lesson learned about the ChromaDB client had to be deliberately applied to the Google API client as well. A lesson learned is only worth what you apply it to next.

# Diagnostic note: log what the system says, not just what it computes

For most of this project the logs showed LLM replies ([llm] Claude replied:) but not scheduler-generated ones — booking prompts, rejections, confirmations were invisible. Diagnosing messaging problems meant switching to the browser and reading the chat.

Fixed by wrapping the handler so every exit path is logged:

python
def handle_booking(phone, message, config, business_id):
    reply = _handle_booking_inner(phone, message, config, business_id)
    print(f"[scheduler] Reply: {reply!r}")
    return reply

The wrapper pattern matters because the function has many return statements — a print before any single one would miss the others.

Rule: if a component produces user-facing output, log that output. The internal state tells you what the code decided; only the output tells you what the customer actually experienced, and those can differ.

## A forgiving error handler turned a real failure into a reported success

**Symptom.** Testing the cancel failure path: the calendar ID was deliberately
corrupted, then an appointment was cancelled from the admin portal. Expected a
red error and the appointment left untouched. Got a **green success message**,
the appointment marked cancelled, and this in the log:

```
[calendar] Event bjbb335sphukfdsip5031vv7e8 already gone — treating as deleted
```

The event was not gone. It was still on the owner's calendar.

**Cause.** `delete_event` swallowed both 404 and 410:

```python
if e.resp.status in (404, 410):
    print(f"[calendar] Event {event_id} already gone — treating as deleted")
    return
raise
```

The reasoning was sound in isolation — deletion is idempotent, so "it's
already gone" should count as success rather than an error. The problem is
that **404 has more than one meaning here**. Google returns it when the event
can't be found *and* when the calendar can't be found. A bad `calendar_id`
produces the same status as an already-deleted event, and the handler couldn't
tell them apart.

**Fix.** Forgive only 410:

```python
# Only 410 Gone means "this event existed and is already deleted" — the
# desired end state is true, so treat it as success.
#
# 404 is NOT safe to forgive: it also fires when the CALENDAR itself can't
# be found (bad calendar_id), and swallowing that reports a successful
# cancellation while the real event stays on the owner's calendar.
if e.resp.status == 410:
    return
raise
```

**Why this one is worth remembering.** Most silent bugs in this project
produced *wrong answers* — a date four hours off, a chunk attributed to the
wrong service, an availability check returning True for an occupied slot.
This produced a **false success**: an operation reported as complete that
never happened. That's worse, because there's nothing to notice. A wrong
answer looks odd; a green checkmark looks finished.

**The general rule.** When writing an `except` clause that treats a failure as
acceptable, ask what *else* can produce that same error. HTTP status codes
carry real precision, and grouping them throws it away:

- `410 Gone` — this resource existed and is deleted. Specific. Safe to forgive
  for an idempotent delete.
- `404 Not Found` — something in the path doesn't exist. Could be the resource,
  could be its container, could be a typo in a URL.

Broad exception handling has the same hazard. `except Exception: pass` around
an external call will happily swallow a config error, a network failure, and
an authentication problem alongside the one benign case it was written for.

**Testing note.** This was only caught because the failure path was tested
deliberately — corrupting the calendar ID and checking that nothing changed.
The happy path passed fine and would have shipped. **Error handlers need
their own tests, and the assertion is usually that nothing happened.**

# Debugging Lessons — Knowledge Base Editor

Append to `notes/debugging_lessons.md`.

---

## Substring keyword matching steals questions from better answerers

**Symptom.** A customer asked *"do you fix drywall if you have to tear it out
to address a plumbing issue?"* and got back the shop's street address. The
newly-added Drywall Repair section was never consulted.

**Cause.** One line in the log:

```
[rules] matched rule: location
```

The location rule listed `address` as a keyword with `match: "any"`, which
did substring matching:

```python
if any(keyword in text for keyword in keywords):
```

The message contains *"to **address** a plumbing issue"* — the verb, not the
noun. The rule fired, returned a canned reply, and the message never reached
retrieval.

**Fix, two parts.**

Match on word boundaries rather than substrings:

```python
if any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in keywords):
```

And replace ambiguous single words with unambiguous phrases:

```yaml
keywords: ["where are you", "your location", "your address", "directions"]
```

Both were needed. Word boundaries alone wouldn't have helped here — "address"
*is* a whole word in that sentence, just a different part of speech. The
phrase change is what actually fixed it; the boundary matching prevents a
different class of the same problem (`close` matching inside `closet`).

**The pattern this belongs to.** This is the **fourth** time an eager keyword
rule has stolen a question the LLM would have answered better:

1. A price rule keyed on "how much" caught "how much notice do you need"
2. A booking rule caught "lets order a dozen cupcakes" as a command
3. A booking rule caught "I'd like to order a cake for Friday"
4. A location rule keyed on "address" caught "to address a plumbing issue"

Keyword rules are fast and free, and they cannot tell what a sentence is
*about*. Each individual fix is easy; the recurrence is the signal. The
standing item in `future_directions.md` — let the LLM detect booking intent
and hand off with slots pre-filled — would remove the whole class rather than
patching instances.

**Rule of thumb:** a keyword is only safe if it's unlikely to appear in a
sentence about something else. Single common words rarely pass that test;
short phrases usually do.

---

## "Nothing changed" must be true in every path that says it

**Symptom.** Testing the publish failure path with a deliberately corrupted
API key produced exactly the intended behaviour — an error flash reading
*"Publishing failed. Your previous content is still live"* and the
unpublished-changes banner correctly staying put.

The message was false. The knowledge base was empty.

**Cause.** `ingest_documents` deleted the existing collection before
rebuilding it:

```python
client.delete_collection(collection_name)      # old content gone
collection = client.get_or_create_collection(...)
collection.add(documents=chunks, ...)          # fails here
```

A failure between those lines leaves the business with no knowledge base at
all. The error handling was correct — it reported failure, it didn't clear the
dirty flag — but the *reassurance* attached to it wasn't.

**Fix.** Build into a temporary collection and swap only after every embedding
call succeeds:

```python
temp = client.get_or_create_collection(name=f"{slug}__building", ...)
try:
    temp.add(documents=chunks, ...)      # if this fails, live is untouched
except Exception:
    client.delete_collection(temp_name)
    raise

client.delete_collection(collection_name)
live = client.get_or_create_collection(name=collection_name, ...)
live.add(documents=chunks, ...)
client.delete_collection(temp_name)
```

Trade-off worth knowing: ChromaDB has no rename, so the swap re-embeds
everything a second time — double the API calls. Trivial at 8 chunks, not at
800. The cheaper alternative is to embed a single chunk first as a smoke test,
then rebuild; that catches bad keys, exhausted quota and outages, but not a
failure partway through a large batch.

**The general rule.** A destructive operation followed by a rebuild is not
atomic, and any message claiming the previous state survived is a lie in the
window between them. Either make it atomic, or make the message honest.

**Related, and worth noticing:** this is the same family as the 404/410 bug,
where an over-broad `except` reported a failed cancellation as a success. Both
are cases where the code's *report* diverged from reality, and both were only
caught by testing the failure path deliberately. A wrong answer looks odd; a
confident reassurance looks finished.

---

## A placeholder comment in pasted code is a runtime error waiting to happen

**Symptom.** `Publishing failed: name 'chunks' is not defined.`

**Cause.** A rewritten function was supplied with `# ... assemble chunks, ids,
metas exactly as before ...` standing in for a block that was meant to be kept
from the original. It was pasted literally, so the variables were never built.

Harmless here — it failed loudly, before anything destructive, and the error
named the missing variable. But worth noting the shape: **an elision in code
you're pasting is an instruction to yourself, and the interpreter doesn't read
comments.** When replacing a function wholesale, it's safer to work from a
complete version than to reconstruct one from a diff and a placeholder.

## Wrapper Functions and Pass-Through Parameters

A wrapper that accepts a parameter and hardcodes it discards it silently

Context. handle_booking is a thin wrapper around _handle_booking_inner, added so every scheduler reply gets logged regardless of which of the many return statements produced it. Adding LLM intent detection meant threading a new prefilled argument through both.

The first symptom was loud:

TypeError: handle_booking() got an unexpected keyword argument 'prefilled'

Only the inner function had been updated. Easy fix, and Python named it precisely.

The second would have been silent. The wrapper's call to the inner function was:

python
reply = _handle_booking_inner(phone, message, config, business_id, prefilled=None)

Adding prefilled=None to the wrapper's signature fixes the TypeError and leaves this line still passing a hardcoded None. No error. The booking flow would simply ask for a service the customer had already given — which is the exact behaviour the feature existed to remove, failing in a way that looks like the feature not working rather than a bug.

Fix. The wrapper must pass what it received:

python
def handle_booking(phone, message, config, business_id, prefilled=None):
    reply = _handle_booking_inner(phone, message, config, business_id, prefilled)

The general shape. A wrapper's job is to add one behaviour and otherwise be invisible. Every parameter it doesn't forward is a silent hole. When adding an argument to a wrapped function, there are always three places to change — the inner signature, the outer signature, and the call between them — and only the first two produce errors when missed.

Worth checking with a grep whenever a wrapper is involved:

bash
grep -n "def handle_booking\|def _handle_booking_inner\|_handle_booking_inner(" scheduler.py

Three lines, and all three should mention the new parameter. That check found this in seconds; reading the file would have taken longer and might have missed the hardcoded default, since prefilled=None looks entirely reasonable in isolation.

Related pattern in this project. Same family as the earlier sorted()-before-[:3] bug in find_alternatives: every individual piece was correct, and the composition threw the result away. Both are cases where the type checker and the interpreter are satisfied, and only the end-to-end behaviour reveals the problem.
## Logging replaced print (2026-09-11)

`print()` writes to stdout and forgets: no level, no timestamp, no way to
quiet the chatty lines in production or turn them up while debugging, and
on Render the output dies with the process.

The conversion rule was mechanical, because the old prefixes were already
doing a logger's job: **`print(f"[rag] ...")` became
`log.info(f"...")` with `log = logging.getLogger("rag")`.** The `[rag]` in
the output now comes from the logger name instead of being typed into every
string, so every `grep "\[rag\]"` still works.

Three things worth knowing:

- **Levels are a filter, not decoration.** `LOG_LEVEL=DEBUG` in `.env`
  turns on the per-request noise (raw LLM output, RAG distances) with no
  code change. Default is INFO. The chatty lines were set to DEBUG during
  conversion precisely so INFO stays readable.
- **The turn id is what makes the log usable.** One customer message fans
  out into a dozen lines from five modules, and under gunicorn two
  customers interleave. `new_turn()` at each entry point puts a short id in
  a `ContextVar`, and a `logging.Filter` stamps it on every record — so
  `grep 9781 logs/app.log` gives one customer's whole turn in order.
  ContextVar rather than a global because a global is shared between
  threads and would hand you two customers' lines under one id.
- **File logging is best-effort.** If the log directory can't be created
  the app still starts and logs to console. A logging failure must never be
  an outage.

### The bug the conversion introduced, caught on first restart

`[rag] Using Voyage AI embeddings.` disappeared from the startup output.
No error, no traceback — one line that used to be there, wasn't.

Cause: `setup_logging()` was called *after* app.py's own imports. rag.py
logs that line at module level, so it fires **during** the
`from llm import ...` statement, before any handler exists. Logging doesn't
error when it has nowhere to send a record; it falls back to a last-resort
handler that passes only WARNING and above, and drops the INFO line in
silence. Moving `setup_logging()` above the project imports fixed it.

The general rule: **configure logging before importing anything that logs
at import time.** Stdlib imports first, then logging setup, then everything
else. And note the failure mode — a missing line rather than a crash, which
is the same plausible-but-wrong shape as the other bugs in this file.

A second thing went wrong at the same time: `werkzeug` had been pinned to
WARNING as "noise", which silently took the dev-server URL and the debugger
PIN with it. Quieting a third-party logger wholesale throws away its useful
lines along with its boring ones.

Prints that stayed prints: the CLI blocks in `rag.py`, `seed_businesses.py`,
`project_stats.py` and friends. Those talk to a person reading a terminal
right now, which is exactly what `print` is for. Logging is for the record
you read later.

---

## The slow request that undid three fast ones (2026-09-16)

**Symptom.** A Bob's Plumbing chat went haywire: the bot asked "Briefly,
what's the issue?" twice, complained "I couldn't read that as a date and
time" in reply to the word "no", and told a customer three questions into a
booking to "text 'appointment'" to start booking. Four separate-looking
bugs.

**What the log said.** One turn id told the whole story:

    19:01:03  9137  [webchat] Bob's Plumbing <- web_32rp96w6: 'How about Tomorrow?'
    19:02:59  9137  [calendar] Early availability check failed: The read operation timed out
    19:02:59  9137  [scheduler] Reply: "Briefly, what's the issue?"

That request ran for **116 seconds**. In the gap, three other turns
(f361, 253e, 0c61) read the state, advanced it, and saved. Then 9137 came
back and wrote its 19:01:03 `pending` over the top — deleting two answers
the customer had given in the meantime and re-asking a question they'd
already answered.

**Three causes, one incident.**

1. *No timeout on the Google Calendar transport.* httplib2 waits on a
   socket forever unless told not to. A booking that fails fast is
   recoverable; one that hangs is not. Now `CALENDAR_TIMEOUT_SECONDS`
   (default 10).
2. *Last-writer-wins state.* `set_state` was an unconditional
   `INSERT OR REPLACE`. A state machine whose writes don't check what
   they're overwriting isn't a state machine, it's a race. Now
   `conversation_state.revision`: a turn reads a revision, every write
   checks it, and a turn whose write is refused throws its reply away
   rather than sending something about a conversation that has moved on.
3. *The browser raced itself.* The Send button was disabled while waiting;
   Enter wasn't. The customer, facing silence, pressed Enter four more
   times. The server should survive that — and now does — but the honest
   fix is also not to start the race.

**The lesson worth keeping.** Four weird replies, one cause. The instinct
was to fix each symptom where it appeared — a better date error here, a
better prompt there — and every one of those fixes would have been real
work that left the bug in place. What found it was reading one conversation
in the log by *turn id* rather than by timestamp. Timestamps interleave;
turn ids don't. A log you can't group by turn is a log that shows you four
bugs where there is one.

**Two genuine bugs it was hiding**, found in the same pass and fixed
separately: an unparseable date stayed in the slot (so the slot counted as
filled and the complaint arrived three questions late), and the mid-booking
Q&A prompt had no idea a booking was in progress, so it kept telling people
to start one.

**Also a reminder about blanket replacements.** The regex that routed every
`set_state(phone, business_id,` call through the new guard also rewrote the
call *inside* the guard, making it call itself. Same mistake as the
`AFFIRMATIVE` replace in August. Reading the resulting function is what
caught it; the compiler never would have.

---

## The decorator that slid onto the wrong function (2026-09-17)

**Symptom.** `werkzeug.routing.exceptions.BuildError: Could not build url
for endpoint 'admin.login'. Did you mean 'admin._login_ip' instead?` — on
any attempt to reach the login page.

**Cause.** In the rate-limiting work (653fac2) I added a `_login_ip()`
helper and inserted it directly beneath the existing

    @admin_bp.route("/login", methods=["GET", "POST"])

decorator, which was sitting above `login()`. A decorator binds to whatever
function comes next, so `_login_ip` became the login view. Flask was
entirely happy: `GET /admin/login` returned the caller's IP address as the
page body, and no endpoint named `admin.login` existed any more.

**Why it survived a commit, a push and a week.** Nothing visits the login
page while you're logged in, and `url_for("admin.login")` is only built by
`login_required` when someone *isn't*. Casey had a live session throughout,
and the demo flow signs visitors in by setting the session directly rather
than by posting the login form. The first person to be logged out was the
first person to see it — which happened because a demo sweep deleted the
demo user his session pointed at.

**Why no test caught it.** A BuildError is raised at render time, by the
specific page that references the missing endpoint. Unit tests don't reach
it, the conversation harness doesn't render templates, and the app starts
perfectly — Flask doesn't validate that url_for targets exist until one is
built.

**What now catches it.** `routes_test.py` — a static check, no Flask import
and no running server. It reads the route decorators with `ast` (so it sees
what Flask will *actually* register, not what the code looks like it
means), collects every `url_for()` in the Python and the templates, and
fails on any reference with no matching endpoint. It also fails any route
attached to a function whose name starts with an underscore, which is the
shape of this exact accident. Verified by putting the bug back and watching
both checks go red.

**The lesson.** Two of this week's bugs — this and `bootstrap()` never being
called — are the same thing: code that is *defined* but not *wired*, where
the wiring is invisible at rest and only observable by exercising the exact
path. Import-time correctness proves nothing about registration-time
correctness. When something is connected by a decorator, a naming
convention, or a call from somewhere else entirely, there should be a check
that the connection exists — not just that the code does.

---

## The demo that signed me out of my own account (2026-09-18)

**Symptom.** "The admin page is broken now. It is acting like the demo
page." Every admin URL either showed a demo business or redirected to one.

**It wasn't broken.** One line of the access log had the whole answer:

    19:18:05  GET /admin/  ->  302  ->  /admin/business/13/appointments

Business 13 is `demo-ridgeline_contracting-0d8663`. Clicking "Explore this
one" on the picker runs `demo_start`, which signs the visitor in as the
clone's owner by setting `session["user_email"]` — **replacing whatever
session was already there.** An operator who clicked a demo out of
curiosity became a demo owner, and `/admin/` then correctly redirected that
non-operator to their own business. Every component behaved exactly as
designed. The session simply wasn't who I thought it was.

**Why it surfaced only now.** The nav had shown a "Businesses" link
unconditionally, so the wrong session was survivable: you clicked it, got
the dashboard, and never noticed you'd been logged out of your own account.
Making that link operator-only — a cosmetic change — removed the cover and
the underlying problem became visible. A tidy-up exposing a real bug is a
good trade, but worth recognising for what it is: the tidy-up didn't cause
it, it stopped hiding it.

**Three fixes, because "invisible" was the actual defect:**

1. A demo portal now carries a banner saying it's a sandbox. Before, it was
   pixel-identical to a real portal apart from one nav link — which is how
   an operator ends up editing a demo believing it's a client.
2. `demo_start` records whose session it displaced, and the nav offers
   "Back to your account", naming the account by email.
3. The wordmark and "Businesses" now resolve by role instead of pointing at
   a dashboard that bounces anyone who isn't an operator.

**The lesson.** An action that changes *who the user is* — or what they're
looking at — has to say so on screen. Not in a log, not by implication.
`demo_start` did exactly what its code said and what its comment promised;
nothing anywhere told the person it had happened to them.

---

## Meta-lesson: four bugs in one week, all living in a connection

Worth writing down together, because individually each looked like a
different kind of mistake and collectively they are one:

| what | where it lived |
|---|---|
| `bootstrap()` defined, documented, maintained, never called | a call that didn't exist |
| `@admin_bp.route("/login")` bound to the helper beneath it | a decorator attached to the wrong thing |
| `prune_rate_limits()` written, never called | a call that didn't exist |
| `demo_start` silently replacing a live session | an effect with no signal |

None is a logic error. Every one of them compiled, imported cleanly, passed
the suite, and read correctly in review. The bug in each case was not in
any line of code — it was in the **seam between two things**: a definition
and its caller, a decorator and its function, an action and the user's
model of what just happened.

**Seams have no error message.** A function nobody calls raises nothing. A
decorator on the wrong function registers a route successfully. A session
swap completes without complaint. Absence and silence both look exactly
like everything being fine, which is why all four survived commits, pushes,
and in two cases a week of use.

**So the rule: when something is connected by a decorator, a naming
convention, a call from somewhere else, or a user's assumption — write a
check that the connection exists.** Not that the code works; that it is
*attached*. The checks that came out of this week are all that shape:

- `routes_test.py` — does every `url_for` name an endpoint that exists? Is
  any route bound to a function whose name starts with an underscore? Do
  the URLs we've promised to keep still resolve?
- `styles_test.py` — does every class a template uses have a rule behind
  it? Does every `var()` read a token that was actually declared?
- The startup warning for `service_durations` keys that name no service.
- `prune_rate_limits()` returning a count, so a caller can say whether it
  did anything.
- The per-turn `[cost]` line — an effect that used to be invisible until
  the vendor's dashboard caught up the next day.

Each is cheap, static, and catches a class rather than an instance. The
common property: they assert a *relationship*, not a value. Unit tests
verify what a function returns. These verify that anything is calling it.

**Corollary for review:** "it compiles" and "the tests pass" say nothing
about whether the thing is wired up. When reading a diff that adds a
function, a route, a class or a template, the question isn't "is this
right?" — it's "what calls this, and is that visible from here?" Three of
these four would have been caught by asking it.

## Two clocks that agreed on my laptop (2026-09-23)

**Symptom (predicted, never reported):** on Render, a customer at 2pm asks
for 4pm today and is told the time has passed. At 9pm, "tomorrow at 9"
books the day after tomorrow.

**The seam:** appointment times are naive strings in the *business's*
timezone ("2026-09-22 16:00" means 4pm in Rochester). Every "now" they were
compared against was `datetime.now()`, the *server's* local time. On a
laptop in Rochester the two clocks are the same clock, so every test
passed and every demo worked. Render runs on UTC, four hours ahead.

Each line was individually correct. `datetime.now()` returns the right time
for the machine; the stored string is right for the business. The bug is
only in the comparison, and it appears only when the two run in different
places. It surfaced because the eval pinned the prompt's clock and the
question "whose clock is this?" got asked once, then kept getting asked.

A subtler variant was in the Google sync: `datetime.now().replace(tzinfo=tz)`.
It looks timezone-aware, and it is, but it attaches the business's zone to
the server's wall-clock time. That produces a perfectly valid, confidently
wrong timestamp four hours in the future. `datetime.now(tz)` converts;
`.replace(tzinfo=tz)` relabels.

**Fix:** one module (`clock.py`) that reads the real clock in exactly one
place (`utc_now`) and converts to the business's zone. The tests replace
`utc_now` and run the scheduling rules at 6pm and 1:30am UTC, the two
moments where a UTC server and an Eastern business disagree about the hour
and about the date. Run under `TZ=UTC`, the old code fails them.

**The general rule:** a naive datetime is only meaningful next to the
answer to "local to what?". If two naive values come from different places,
one of them is wrong somewhere.

## The fix that broke every booking, and the test that watched the wrong end (2026-09-23)

**Symptom:** on the live demo, every date a customer typed ("Tomorrow at
12", "Friday at 2") got "Sorry, I couldn't read that as a date and time."
It was found within an hour of deploying, by booking on the live site by hand.

**Cause:** the time-zone fix (`50755a5`) trimmed an import in
`parse_datetime` from `datetime, timedelta` to `timedelta`, because the line
it replaced no longer needed `datetime`. Thirty lines further down,
`datetime.strptime` validated the model's answer. Every call raised
`NameError`, and a catch-all `except Exception` logged it at *info* as
"Date parse error", the same line a customer's gibberish produces. So:
no crash, no error in the log, and 100% of bookings refused.

**Why the new test missed it:** `clock_test.py` checked the *prompt*
`parse_datetime` sent to the model, which was the thing the fix changed,
and never the *value it returned*. Its fake client answered correctly. The
function then threw that answer away. The test watched the input end of the
pipe while the output end was broken.

**Fixes:**
- the import, with a comment on why it's needed
- the `except` split: `ValueError` is a customer miss (info); anything else
  is our bug (`log.exception`, with a traceback)
- the test asserts the return value, and was run against the broken import
  to prove it fails

**Lessons:**
1. A test for a function checks what it *returns*, not only what it does
   along the way.
2. A catch-all `except` that logs at info turns a programming error into a
   silent product failure. Catch the error you expect; let anything else be
   loud.
3. The "real conversation after every change" habit caught this in minutes.
   All 309 green checks didn't.
