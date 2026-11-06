# Spend caps: what the model costs each business

Design: `spend_caps_task.md` (2026-11-04). This file is the record: what
was measured before anything changed, what was built, and what it costs.

## 1. Every place the app calls the model (measured before changing any)

`grep -rn "messages.create\|_create(" *.py` on main at ff3eb06. All but one
went through `llm._create` → `llm._sent` → `client.messages.create`, which
charged demos (budget.py) and nothing else.

| # | Where | What for | Model | Business known how |
|---|-------|----------|-------|--------------------|
| 1 | `llm.classify_and_extract` | booking or question, slots (customer turn) | Haiku 5.5 | process_message's `business_id` |
| 2 | `llm.extract_booking_slots` | slots in a booking answer | Haiku 5.5 | same (scheduler) |
| 3 | `llm.parse_datetime` | "Friday at 5" → a time | Haiku 5.5 | same (scheduler) |
| 4 | `llm._reply_using_tools` | the customer's reply, 1-3 rounds with policy tools | Haiku 5.5 | same; also Preview |
| 5 | `assistant.answer` | the owner assistant (Help page and the pop-up: the same function, so "the Help assistant" is this one) | ASSISTANT_MODEL or Haiku 5.5 | the signed-in owner's business |
| 6 | `preview.suggest_questions` | Preview: questions to try | Haiku 5.5 | the URL's business |
| 7 | `preview.compare_facts` | Preview: did the facts change | Haiku 5.5 | same, **in a thread pool** |
| 8 | `grounding.claim_flags` | Preview: claims nothing it read says | Sonnet 5.5 | same, **in a thread pool** |
| 9 | `contradictions.check` | Knowledge page's contradiction check | Sonnet 5.5 | the URL's business |
| 10 | `photo_import._call` (`transcribe`, `transcribe_pages`, `propose_changes`) | photo/PDF reading, web page and re-suggest proposals | UPLOAD_MODEL or Haiku 5.5 | the URL's business |
| 11 | `judge._call` | eval judge: its **own** `Anthropic()` client, plus a plain-HTTP fallback | Sonnet 5.5 | none: evals only |
| - | `assistant_eval.py` | wraps `llm._create` to count tokens | - | eval |

Findings before any change:

- **Preview's thread pool escaped the demo cap.** `budget.check()` and
  `budget.record()` read `flask.g`, which a worker thread doesn't have, so
  Preview's answers (#4), fact checks (#7) and grounding checks (#8, Sonnet)
  were neither checked nor charged to the demo that asked. The door check
  before the pool still stopped an over-budget demo; what leaked was the
  charge.
- `judge.py` bypassed `_create` entirely (fine for money: evals only; but
  invisible to any ledger).
- Real businesses were never capped or counted anywhere but the cost log
  line.
- History resent per customer call: the last 10 messages
  (`get_recent_messages(limit=10)`; the booking flow's extraction uses 6),
  no length cap. A customer message is at most 1,000 characters
  (`app.webchat_reply`), so the worst case is about 10,000 characters of
  history on every call of a turn.
- Prompt caching: on for the owner assistant only (`assistant.py`, the
  owner guide block). The customer reply's system prompt isn't cached, and
  couldn't be as written: its second line is "Today is …, and the time is
  10:42 AM", so the prefix changes every minute. Measured in section 4.

## 2. What was built

- **One wrapper.** `llm._create(purpose=…, business_id=None, **call)` keeps
  the model fallbacks; the one place a request is sent is `usage.send`
  (the judge's SDK and HTTP paths included). `usage_test.py` scans the code
  so nothing else calls `messages.create` or the HTTP endpoint, and every
  `_create(` names its `purpose`.
- **Who pays.** `usage.serving(business_id, config)` names the business
  for everything that follows: `budget.scope_request` does it for each
  request (the URL's business, else the signed-in owner's), and
  `process_message` for each customer turn. It's a ContextVar, so Preview's
  pool gets it by `contextvars.copy_context()` (the leak above is closed:
  the demo cap and the ledger both see Preview's calls now).
- **Evals never count.** Each `*_eval.py` calls `usage.mark_eval_run()`
  (and `judge.py`'s calls are `purpose="eval"`). Their rows still go in the
  ledger (of the eval's throwaway database), as `purpose = eval` with
  `billable = 0`, so their cost can be read but never refuses a business.
- **`usage_ledger`**: one row per call, not one upserted row per
  (business, month, purpose, model). Why: a row per call is what lets
  journey_eval (and later a dashboard) say what one *conversation* cost,
  and lets a spike be traced to the minute it happened; at a few calls per
  message the table stays small, and the month's sum is one indexed query.
  Columns: business_id, month (business-local `YYYY-MM`), created_at,
  purpose, label (the call site), billable, model, input/output/cache read/
  cache write tokens (from the response's `usage`, never estimated), and
  micro_dollars (an integer). No message text, names or numbers.
- **Prices** (`usage.PRICES`, per million tokens: input, output, cache
  write, cache read): Haiku 5.5 0.10/0.50/0.125/0.01, Sonnet 5.5
  2/10/2.50/0.20, Opus 5.5 4/20/5/0.20, Fable 5.1 10/50/12.50/0.25, Haiku 4.5
  1/5/1.25/0.10. $1 per million tokens is 1 micro-dollar per token, so the
  arithmetic is integers in nano-dollars per token. A model not in the
  table logs a warning and is priced as the dearest one. `LLM_PRICE_IN` /
  `LLM_PRICE_OUT` still override the receptionist's model.
- **Budgets.** `budget.monthly_usd` (hard) and `budget.alert_at` (soft,
  dollars; 80% of the hard one when unset), from the operator's setting
  (AI spend page), else the YAML's `budget:`, else
  `DEFAULT_MONTHLY_BUDGET_USD` (default $10). Never unlimited.
- **The door, decided once.** As for demos: a turn (or portal request)
  that starts under budget finishes, so a single turn overshoots by at most
  its own cost (a few hundredths of a cent).
- **Over budget, the customer** gets `budget.over_budget_reply(config)`:
  "Sorry, I can't answer messages here right now. Please call us at
  {phone}{, hours}." (no phone: "please contact {name} directly"). Urgent
  rules still answer first (Miller's "death_just_happened" never calls the
  model), then the canned reply, before the booking flow, so a customer
  mid-booking gets the phone and no booking question.
- **Over budget, the owner**: the portal's model features (owner
  assistant, Preview, contradiction check, uploads) show
  `budget.MONTH_NOTE` through the doors they already had for demos; the
  rest of the portal works.
- **Alerts** (`usage_alerts`, one row per business, period and kind, so
  "once" is a primary key): the soft limit once per business per month,
  the hard limit once per business per month, out of credit at most once
  an hour for the whole account. Each is a Support inbox request through
  `support.file_request`, so it emails when SMTP is set up and is in the
  inbox when it isn't.
- **Out of credit** (the API answers 402 `billing_error`, or a 400 saying
  the credit balance is too low): `usage.OutOfCredit`; logged as an error
  with the API's error type; the customer gets the same canned reply
  (process_message marks the turn and swaps the reply, whatever the inner
  code made of the error), never a 500.

## 3. Owner and operator pages

- Owner, Settings: "AI use this month: $0.42 of your $10.00 monthly limit (4%)".
- Operator, **AI spend** (nav, beside Support): every business, this
  month's spend, budget, % used, a form to set the budget and alert; demos
  summed in one row.

## 4. Cost controls and measurements (2026-11-05)

- **History cap.** `llm.HISTORY_MAX_CHARS` = 4,000 characters of history
  per reply, oldest dropped first. Measured first in the local database:
  the longest real last-10 messages were 962 characters (average 496), so
  no real conversation is trimmed and its prompt is byte-identical; the
  worst case (ten 1,000-character messages) goes from 10,000 to 4,000.
- **Prompt caching.** The owner assistant's guide is cached, and the
  ledger shows it: two questions in a row at Crosstown (throwaway copy of
  the database), the first wrote 10,035 tokens to the cache ($0.00139), the
  second read the same 10,035 back ($0.00029). The customer reply is not
  cached, on purpose: its system prompt is 5,000-6,250 characters (about
  1,500 tokens, $0.00015 a call on Haiku 5.5) and its second line is the
  current minute, so caching it means moving that line, a model-facing
  change, to save well under a tenth of a cent per conversation. Not worth
  the risk to answers now; revisit if the prompt grows or the model's price
  does. The journeys show cache reads 0 for customer conversations, as
  expected.
- **Offline tests:** `run_tests.py` 2,973 passed, exit 0 (59 files, with
  `usage_test.py` 83 checks). Each guard mutation-tested (13 mutants, all
  caught, files restored and `cmp`-checked): the door in process_message,
  the reply swap on refusal, the guarantee at the call, alert-once, eval
  not billable, unknown model priced high, out-of-credit recognised, the
  history cap, customer routes skipping MONTH_NOTE, Preview's copied
  context, a call site's purpose, cache tokens in the cost, one ledger row
  per call. The first mutant (the door) was missed at first: with the door
  gone, the guarantee still produced the phone reply. The test now checks
  an over-budget turn never reaches the call's own check.
- **Journeys** (`journey_eval.py --repeat=3`): 85/90 clean in the full
  run, but a DNS outage hit it mid-run (91 "Connection error" / "Failed to
  resolve" lines; both 0/3 Ridgeline journeys answered "I can't look that
  up right now"). The five failing journeys rerun x3 with the network back:
  5/5, 15/15 runs clean. The three new over-budget journeys pass 3/3: a
  mother just died (Miller's urgent rule, both numbers), a death in words
  the rule doesn't list (the over-budget reply, the Rochester number), and
  mid-booking at Bob's (the phone and hours, no question). No model call,
  no tool, nothing in the ledger for any of those turns.

### What a customer conversation costs (from the ledger)

Each journey run is one conversation; its own calls only (not the judge's).
Haiku 5.5, 2026-11-05, 270 conversations:

| Business | Conversations | Median | Max | Calls (median / max) |
|---|---|---|---|---|
| Belmont Hair Studio | 45 | $0.00107 | $0.00241 | 3 / 8 |
| Bob's Plumbing | 27 | $0.00060 | $0.00117 | 2 / 8 |
| Crosstown Pizza | 36 | $0.00079 | $0.00129 | 3 / 8 |
| Miller Funeral | 30 | $0.00036 | $0.00112 | 2 / 4 |
| Ridgeline Contracting | 21 (9 clean rerun) | $0.00067 ($0.00111 rerun) | $0.00104 ($0.00156 rerun) | 5 / 8 |
| Sunrise Bakery | 111 | $0.00045 | $0.00510 | 2 / 12 |
| **All** | 270 | **$0.00060** | **$0.00510** | |

(Ridgeline's full-run figures are low because the outage cut its calls
short; the clean rerun is the better number.) So a customer conversation
costs about a tenth of a cent, a long booking half a cent: the $10 default
budget is roughly 10,000-15,000 conversations a month. The cap is for
scripts, loops and the account running dry, not for normal use.

## 5. Manual steps for Casey (Claude Console, not code)

1. Create workspaces: **production** (Render), **demo** (if the demo gets
   its own deploy or key), **evals** (local runs). Nothing runs from the
   Default workspace, which can't have limits.
2. A new API key in each; production's in Render's environment, evals' in
   the local `.env`. Revoke the old key once both work.
3. A monthly spend limit on each workspace and an organization limit above
   their sum; a spend notification well below each.
4. Auto-reload (or keep a buffer) and a low-balance alert: an empty
   balance silences every business at once (here: every customer gets the
   phone number, and the Support inbox gets one alert an hour).
5. Optional on Render: `DEFAULT_MONTHLY_BUDGET_USD` (default $10).
