the codebase walkthrough, four files remaining — config.py, phone_utils.py, and the two RAG dev tools. The good news is these are the shortest files in the project, and you've actually touched all four during the refactor, so this is more consolidation than new territory. Let's move through them at a good clip.

File 7: config.py — the loader and the substituter

One-line summary: Loads a business's YAML config into a Python dict on demand, and fills {placeholder} tokens in reply templates with real business values.

Open it up. It's two functions now — and the interesting thing about this file is its history. It's the file that changed the most conceptually during the multi-business refactor while changing the least in line count.

What to notice

The absence is the design. The old version had one load-bearing line: CONFIG = yaml.safe_load(...) at module level — a global that loaded at import time. That single line is what made the whole system single-business. The new version has no module-level state at all. Just two function definitions. When you read a module and see zero globals, that's a signal: this module is a pure toolbox — same inputs, same outputs, no hidden state, safe to call from anywhere with anything.

This is worth internalizing as a pattern: module-level state is a commitment. The moment you write X = load_something() at the top of a module, you've decided there's exactly one X for the lifetime of the process. Sometimes that's right (your Anthropic client in llm.py — one API key, one client). Sometimes it's a trap that only springs later (one CONFIG → can't serve two businesses). The question to ask when you write a module-level assignment: "will there ever be more than one of these?" If maybe, make it a function parameter instead.

yaml.safe_load — the one security-relevant line. We've mentioned it before, but since this is the walkthrough: plain yaml.load() can construct arbitrary Python objects from YAML tags, which means a malicious config file could execute code when loaded. safe_load only builds plain data — strings, numbers, lists, dicts. Right now you write all the YAML yourself, so the threat is theoretical. But the admin portal roadmap includes business owners editing their own configs — the moment config content comes from anyone but you, safe_load stops being theoretical and becomes the thing protecting your server. The safe habit was built before it mattered, which is the only time you can build it cheaply.

substitute() and the triple-brace trick. One line worth actually understanding:

python
text = text.replace(f"{{{key}}}", str(value))

That {{{key}}} looks like a typo. It isn't. Inside an f-string, {{ is an escaped literal {, and }} is a literal }. So {{{key}}} parses as: literal { + the value of key + literal }. When key is "phone", the f-string produces the string {phone} — the exact token we're hunting for in the template. Escaping rules nesting inside each other. If you ever see triple braces in an f-string in the wild, this is what's happening.

The str(value) wrapper is small defensive coding — if a YAML value happens to be a number (someone writes phone: 5855550123 without quotes), .replace() would crash on a non-string. The cast makes substitution shrug that off.

Why substitution lives here and not in rules.py. The placeholders ({business_name}, {hours}) are a config concern — they're part of the contract of what a YAML file is allowed to contain. If the knowledge of "what placeholders exist" lived in rules.py, then scheduler.py (which also substitutes) would need its own copy or an awkward import. Putting it next to load_config means the two halves of the config contract — "what the file contains" and "what the tokens mean" — live in one place. When you add a placeholder, you touch one file.

Question worth pausing on

Why does load_config run on every request rather than caching per business? Honest answer: simplicity won. Reading and parsing a small YAML file takes about a millisecond — three orders of magnitude below the Claude API call that dominates any LLM-path request. A cache ({path: config} dict) would save nothing perceptible and would introduce a new stale-state bug: edit a YAML, and the server serves the old version until restart. You've already been bitten by cached state twice (the ChromaDB stale UUID, the DB reset pattern) — the absence of a cache here means config edits take effect on the next request with no restart. That's actually a feature for the admin-portal future: when a business owner edits their config through the UI someday, it goes live instantly. Don't add caching until you can measure the problem it solves.

File 8: phone_utils.py — one function, one lesson

One-line summary: Converts any phone-number-shaped string to canonical E.164 format at the system boundary.

This is the smallest real file in the project, and you already know its story deeply because you debugged it live — the is_valid_number gate rejecting fictional 555 numbers. So rather than re-tell that, let me use it to name the general pattern it demonstrates, because this file is the purest example in your codebase of a concept worth carrying to every future project.

Canonicalization at the boundary

Every system that accepts data from the outside world faces the same problem: the same thing can arrive in many forms. Phone numbers with dots, dashes, parens. Emails with mixed case. Dates in fifteen formats. Names with trailing whitespace. If you let multiple forms of the same thing penetrate into your system, every downstream component needs to handle every form — or worse, silently treats them as different things (the data-fragmentation bug we prevented, where one customer becomes three).

The fix is always the same shape: pick one canonical form, convert at the door, trust everywhere inside. Your app.py calls normalize_phone() in exactly one place — the webhook parse — and from that point, every function in the system can assume E.164 without checking. db.py uses it as a key without validating. The scheduler uses it without thinking. That trust is only safe because the boundary is airtight.

The design decision you fought for during debugging — normalize ≠ validate — is the second half of the lesson. Normalization answers "what's the canonical form of this?" Validation answers "is this real?" Coupling them broke your tests; separating them fixed it. Google's library was right to be strict about validity; your normalizer was wrong to ask it. Different questions, different functions, even when one library offers both.

One thing worth noticing fresh: the pass-through behavior on failure. Unparseable input returns unchanged rather than raising or returning None. That's a judgment call with a rationale: a garbage From value shouldn't crash message handling — better to log the conversation under a weird key than lose it entirely. The trade-off is that garbage keys can exist in your DB. For a receptionist bot, grace beats strictness. For a payments system, you'd flip that decision. The shape of the choice — "on bad input: crash, null, or pass through?" — recurs in every boundary function you'll ever write, and the right answer is domain-dependent.

Files 9 & 10: rag_explore.py and rag_inspect.py — the tools you kept

One-line summaries: rag_explore.py is a self-contained embedding playground (in-memory ChromaDB, ten sentences, five test queries). rag_inspect.py dumps every chunk in the active business's collection so you can see what retrieval actually sees.

These get a shorter treatment — not because they're unimportant, but because their importance is categorical, not line-by-line. They represent a kind of file that most tutorials never mention and most working engineers accumulate constantly: diagnostic tooling that lives alongside the product.

What each one is for

rag_explore.py answers the question "how do embeddings behave?" — it's a learning instrument. It has no dependency on your bot at all (its only import is chromadb; it builds its own throwaway in-memory collection). You used it in Stage 6 to build intuition: seeing "hours of operation" match "What are your business hours?" at low distance, and "what's the meaning of life" match nothing — that's where your feel for the distance threshold came from. It still earns its place in the repo because six months from now, when you're tuning retrieval for a new client's weird domain vocabulary, the fastest way to test "will the embedder consider these two phrasings similar?" is to drop them into this script. Thirty seconds, no server, no ingestion.

rag_inspect.py answers "what is actually in the database?" — it's a production window. It caught the orphan title chunk. It confirmed the chunker fix. It's the RAG equivalent of SELECT * FROM — and note that it needed updating during the multi-business refactor (you caught that its rag._collection reference broke) precisely because it's a real consumer of your module's internals. That's a useful signal, actually: when refactors break your debug tools, the tools are genuinely coupled to the code, which means they're testing something real.

The categorical lesson

There's a distinction worth naming between three kinds of code in any project:

Product code — what runs when customers use the thing (app.py through db.py)
Operational code — what you run to manage the thing (seed_businesses.py, the ingest __main__ block)
Diagnostic code — what you run to understand the thing (these two files, your check_messages-style one-liners)

Beginners write only #1. The debugging sessions we've had were fast because #3 existed — remember that isolating normalize() in a one-liner cracked the phone bug in one step, and rag_inspect.py settled the chunker question in one run. The habit worth keeping: when a debugging session forces you to write throwaway inspection code, consider not throwing it away. Name it, comment it, commit it. The next bug in that subsystem will use it. Your instinct to keep these two files (and to double-check whether they needed updating in the refactor) was exactly this habit forming.

The one hygiene rule for diagnostic files: keep them honest about what they are. Both files open with a comment saying "not part of the bot" — that matters, because a newcomer reading the repo (or you in a year) needs to know instantly which files are load-bearing. Diagnostic tools that look like product code are a real source of confusion in older codebases.

The walkthrough is complete

That's all ten files. The full tour, compressed to one line each:

File	        Role
app.py	        Channel adapters + orchestration; security at the door
rules.py	    Data-driven fast path; three-way return contract
llm.py	        Prompt assembly, Claude calls, structured extraction
rag.py	        Ingest + retrieve; per-call collections; slug as identity
scheduler.py	Persistent state machine; transversal cancel
db.py	        All SQL; the volatile/durable boundary
config.py	    Stateless loader + placeholder contract
phone_utils.py	Canonicalization at the boundary
rag_explore.py	Embedding intuition instrument
rag_inspect.py	Window into the vector store