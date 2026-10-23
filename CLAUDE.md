# CLAUDE.md

**Simon Says**: a multi-business AI receptionist. Flask, RAG (Chroma +
Voyage embeddings), Claude Haiku 4.5 for replies, deployed on Render:
https://simonai-q92o.onrender.com/demo (`/version` shows the deployed
commit). Five demo businesses (`config/<slug>.yaml`, `documents/<slug>/`):
Bob's Plumbing, Belmont Hair Studio, Crosstown Pizza, Ridgeline Contracting,
Sunrise Bakery. Owner portal under `/admin`; demo visitors get their own
throwaway copy of a business.

Casey's portfolio project (aiming at forward-deployed engineering). Explain
changes in plain language; analogies and pictures help. Casey runs pushes
and Render deploys: commit, but don't push unless asked.

## Run it

```bash
source venv/bin/activate
python3 app.py                    # http://127.0.0.1:5001 (not 5000: macOS AirPlay)
python3 run_tests.py              # offline, ~20s, ~1800 checks; exit code is the verdict
python3 run_tests.py policy menu  # only files whose names match
```

**Check the exit code** of `run_tests.py`; never judge it through `| tail`,
which has hidden failures before.

Evals call the API and cost money (a few cents each is fine):

```bash
python3 journey_eval.py ["name filter"] [--repeat=3]   # whole conversations, live path
python3 rag_eval.py <slug> | --all [--retrieval]       # retrieval + answers per business
python3 upload_eval.py | assistant_eval.py | qa_eval.py | conversation_eval.py
```

The live model isn't pinned, so model-facing changes get `--repeat=3`.

## How we work

1. **Measure first.** Reproduce a reported failure as a journey or eval row
   (baseline), then fix, then re-run. Two of four suspected opening-hours
   bugs turned out to be already handled; measuring first saved building them.
2. **After a change:** `run_tests.py` exits 0 → the relevant eval ×3 → the
   whole `journey_eval.py` once if a shared path changed (`llm.py`,
   `policy.py`, `conversation.py`, `scheduler.py`, `rules.py`).
3. **Mutation-test every new guard:** break it on purpose, confirm a test
   fails, restore (back the file up first, `cmp` after).
4. **Commits** are authored as Casey:
   `git -c user.name=Casey -c user.email=caseycaudle@gmail.com commit`.
   The message says what failed (with the evidence: the eval line or live
   reply), what changed, and the tests.
5. **When an eval fails, decide who's wrong** before fixing: the assistant,
   or the check. Many failures were the check (a regex catching a true
   sentence, a rubric quoting only part of a rule).

## Design principles (each learned from a real failure)

- **The model phrases; code settles facts.** Arithmetic and rule lookups go
  through tools in `policy.py` (`check_notice`, `cancellation_outcome`,
  `add_business_days`) and `menu.py` (`menu_price`). Facts the customer
  stated are read in code before a tool runs
  (`policy.arguments_from_words`: which service, how many hours).
- **Prompt rules alone don't hold.** When the model keeps producing the same
  wrong phrase, add a deterministic check on the reply after it's written:
  `llm.plain_text`, `llm.without_repeated_number`,
  `policy.without_false_hope`, `policy.one_off_said_as_one`.
- **Evals check facts, not wording.** Require the tool call (`"tool":` in a
  journey turn); judge meaning with a rubric (`rubrics.py`, quoting the
  WHOLE rule, all windows); keep regexes for exact canned text only.
- **Tools only when needed** (`policy.needs_tools`); every other prompt stays
  byte-identical. Offering extra tools changed unrelated answers.
- **One source of truth.** Cancellation rules live only in
  `policies.cancellation` (YAML or the owner's override), menu prices only in
  `menu:`, opening hours only in `calendar.scheduling.business_hours` (the
  text customers hear is made from it; documents don't state hours). Don't
  state the same fact in two knowledge-base sections.
- **Owner changes are drafts until Publish** (`*_draft` config overrides,
  `documents_dirty`); Preview answers uses drafts.
- **Keyword shortcuts** (`rules:` in each YAML, `rules.py`) answer before the
  model: whole words, phrases not ambiguous words ("hours" caught "in 30
  hours"), and an `unless` list to step aside.
- **Portal JavaScript is progressive enhancement:** pages work without it.
- **Privacy:** never log customer content; an owner's Calendar block notes are
  never loaded into the assistant's config.
- **Security:** CSRF token on every POST form; URL fetches go through
  `web_import.py` (SSRF guards); hidden text in uploads never reaches the
  model. See `notes/security.md`.

## Data

- `chatbot.db` (gitignored) is the local database; `journey_eval.py` copies it.
- `documents/<slug>/*.md` only **seed** an empty knowledge base
  (`import_documents.py`); after that the database is authoritative.
  Render's disk is wiped on each deploy, so the live site rebuilds from the
  seed files. When you edit a seed document, update the local DB to match
  (`db.update_document_section`, then `db.set_documents_clean`) and check
  every section is identical.
- SQLite writes delete a journal file; if a write fails with "disk I/O
  error", that's a delete permission problem, not a bad disk.

## Map

- `app.py`: routes for chat, SMS, demo, `/version`. `conversation.py`:
  `process_message`, the path every channel takes.
- `llm.py`: system prompt, Q&A replies, tool loop, reply checks.
  `scheduler.py`: the booking flow. `rules.py`: keyword shortcuts.
- `policy.py`: rule tools, opening hours and blocks, reply checks.
  `policy_form.py`: the owner's cancellation-rules form (per-service cards).
  `notice_form.py`: the owner's booking-notice form (`booking.notice`).
- `menu.py`, `menu_form.py`: menus as data. `photo_import.py`,
  `web_import.py`: uploads (photos, PDFs, web pages).
- `admin/`: owner and operator portal (`routes.py`, `support_routes.py`,
  templates). `support.py`: Help requests and demo feedback → Support inbox
  (+ email when SMTP env vars are set).
- `preview.py`: Preview answers (Now vs After you publish). `grounding.py`:
  marks a fact in an answer that nothing it read says (numbers in code,
  claims by Sonnet 5.5); measured by `grounding_eval.py`.
- `rag.py`, `import_documents.py`, `db.py`, `config.py`, `demo.py`.
- Deeper reading: `notes/codebase_walkthrough.md`,
  `notes/debugging_lessons.md`, `notes/conversation_defects.md`.

## Open work

See `notes/backlog.md`.
