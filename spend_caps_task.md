# Task: per-business spend caps (usage ledger, budgets, fallback)

For Claude Code in ~/chatbot. Read CLAUDE.md and notes/backlog.md first.
Add this as a backlog item; this file is the design.

## Why

Every model call is paid from one Anthropic account. Normal use per business is
small (owner assistant ~$0.003 a question on Haiku), so the cap exists for
failure cases: a script hammering a chat widget, a loop bug, or a long
conversation. Anthropic's own limits work per workspace, not per business in
this app, so the per-business cap has to live here. **What the receptionist
says when the cap is hit matters more than the cap itself:** for a funeral
home it must still give the phone number.

## Measure first

1. Find every place the app calls the model (`messages.create` or the SDK's
   equivalent): the customer reply (`llm._create`), the classifier, the owner
   assistant, the Help assistant, preview's `compare_facts`, photo/PDF reading
   and proposing, and anything else. List them in notes before changing any.
2. Route every one through a single wrapper (extend `_create` or add
   `usage.call(...)`) that takes `business_id` and a `purpose` label
   (`customer_reply`, `classifier`, `owner_assistant`, `preview`, `upload`, …).
3. Evals (`judge.py`, rag_eval, journey_eval, etc.) are tagged `purpose=eval`
   and never count against a business budget.

## The ledger

- New table `usage_ledger`: business_id, month (business-local `YYYY-MM`,
  via `clock.business_now`), purpose, model, input_tokens, output_tokens,
  cache_read_tokens, cache_write_tokens, cost in micro-dollars (integer, no
  floats in money). One row per call, or one upserted row per
  (business, month, purpose, model): pick one and say why.
- Token counts come from the response's `usage` field, never estimated.
- Prices: one table in code per model (input, output, cache read, cache
  write), overridable by the existing `LLM_PRICE_IN` / `LLM_PRICE_OUT`. A
  model missing from the table logs a warning and is priced at the most
  expensive known rate, so an unknown model can't read as free.
- No message text, names or phone numbers in the ledger (personal data stays
  out).
- Demo tenants: ledger rows under the demo's own id, plus a per-visitor cap
  (backlog: "a cap on model spend per demo visitor"). Pruned with the demo.

## Budgets

- Per-business settings: `budget.monthly_usd` (hard) and
  `budget.alert_at` (soft, default 80% of hard). Never set = a default from
  an env var (`DEFAULT_MONTHLY_BUDGET_USD`), not unlimited.
- Operators set budgets; owners see their usage on Settings (this month's
  spend as a share of the budget, no raw token counts needed).
- Operator page: every business, this month's spend, budget, % used.
- Check BEFORE each call (spend so far + nothing reserved; a single call can
  overshoot by its own cost, which is fine).

## When a business is over budget

- **Customer chat (web + SMS):** a canned reply, no model call, built from
  the business's config: "I can't help in this chat right now. Please call
  us at {phone}{, hours if known}." Same pattern as the existing canned
  replies. It must never be empty and must always include a phone number;
  if the config has none, fall back to "please contact {business name}
  directly". Booking flow steps that need the model also get the canned
  reply; don't leave a customer mid-booking without a way out.
- **Owner assistant / Help assistant / preview / uploads:** a plain message
  that the monthly AI limit is reached, with the hand-off to support. Pages
  that don't call the model (Publish, Discard, Menu, Settings) keep working.
- **Soft limit:** one alert per business per month to the operator, through
  the existing support-alert email path (SUPPORT_ALERT_EMAIL). Also a line in
  the Support inbox, so it works before email is set up.
- **Anthropic out of credit / spend limit reached** (the API refuses the
  call): same canned customer reply, logged as an error with the API's
  error type, and one operator alert per hour at most. This covers the case
  where the whole account runs dry, not just one business.

## Other cost controls (small, same task)

- Cap how much conversation history is resent per call (history grows every
  turn). Check what's sent today before changing it; run journey_eval after.
- Confirm prompt caching is on for the large stable parts (system prompt,
  owner guide) and that the ledger shows cache reads.

## Tests

- `usage_test.py`:
  - No model call outside the wrapper: scan the code like `startup_test.py`
    does for ChromaDB.
  - Cost arithmetic per model, incl. cache tokens; unknown model priced high.
  - Over budget → canned reply, no model call made (fake client counts
    calls); the reply contains the phone number for every config in
    `config/`.
  - Soft alert fires once per month, not per message.
  - Eval-tagged calls never touch a business budget.
  - API out-of-credit error → canned reply, not a 500.
  - Mutation-test each guard (break it, watch the test fail).
- `journey_eval.py`: a journey "over budget": a business with its budget
  used up, customer says their mother just died → reply has the phone
  number, no tool calls, no booking questions.
- Run `run_tests.py`, then journey_eval `--repeat=3`. Check the ledger
  afterwards and report the real cost per customer conversation (median and
  max), per business. That number goes in the notes.

## Manual steps for Casey (Claude Console, not code)

1. Create workspaces: **production** (Render), **demo** (if the demo gets
   its own deploy or key), **evals** (local runs). Nothing runs from the
   Default workspace, which can't have limits.
2. New API key in each; put production's in Render's environment, evals'
   in the local `.env`. Revoke the old key once both work.
3. Set a monthly spend limit on each workspace and an organization limit
   above their sum; add a spend notification well below each.
4. Turn on auto-reload (or keep a buffer) and a low-balance alert: an empty
   balance silences every business at once.

## Out of scope

Charging businesses (Stripe) — later; the ledger is what it would read from.
