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