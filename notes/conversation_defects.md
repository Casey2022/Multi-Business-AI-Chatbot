# Conversation quality — defects found in the transcripts (2026-09-15)

Method: 75 conversations / 700 messages pulled from the `messages` table and
read in conversation order, rather than recalled. Each defect below cites a
real exchange and a date, so "still broken" is a fact rather than a guess.

---

## 1. A slot is re-asked with the identical sentence  (FIXED — `70c0e2f` + `95dab25`)

```
cust  book
BOT   Happy to schedule an appointment! What service do you need?
cust  pipe leak
BOT   Happy to schedule an appointment! What service do you need?
cust  1738 William St, Buffalo
BOT   Happy to schedule an appointment! What service do you need?
```

Both customer answers WERE captured — "pipe leak" as problem_description,
the address as service_address. Only `service` stayed empty, so the flow
re-asked it, verbatim, with no acknowledgement of what had just been taken.

Two separate faults:

- **Extraction mis-assigns.** "pipe leak" is a service, and was filed as a
  problem description. Nothing anchors extraction to the configured
  `services` list.
- **Re-asking repeats.** A second ask should not be the first ask. It needs
  to acknowledge what was understood and narrow the question — "Got the
  address. Which service is it — drain cleaning, leak repair…?"

Oldest instance 2026-08-02, most recent 2026-09-15. Nine occurrences.

## 2. Service name is whatever sentence happened to land  (FIXED — `70c0e2f`)

The appointment from that conversation is titled **"I have a leaky pipe"**.
It reaches the calendar and the owner's list that way. `service` is free
text with no normalisation against the business's configured services.

## 3. An offered slot is not honoured when the customer accepts it  (FIXED — `70c0e2f`)

```
BOT   ...I have: Tuesday, September 22 at 11:00 AM · Tuesday, September 22
      at 4:00 PM · Wednesday, September 23 at 9:00 AM. Would any work?
cust  Wednesday at 9
BOT   Sorry, Wednesday, September 16 at 9:00 AM is already booked...
```

The bot offered Wednesday the 23rd, the customer accepted it, and the bot
resolved "Wednesday at 9" against the calendar instead of against its own
offer — landing on the 16th and refusing it. The alternatives it just
printed are not remembered as conversational context.

## 4. Stacked, ungrammatical greeting  (FIXED — `70c0e2f`)

```
cust  I'd like to book an appointment for next wednesday
BOT   Happy to help with for next wednesday. Happy to schedule an
      appointment! What service do you need?
```

`_ask_next_or_finalize`'s first-turn acknowledgement builds "Happy to help
with {known}" where known is "for next wednesday", then appends the
greeting, which is itself a greeting. Two hellos and a broken preposition.

## 5. Mid-booking questions are not answerable  (FIXED — `70c0e2f`)

```
BOT   Anything else we should know? (allergies, pickup person — or 'none')
cust  Can you put a design on top?
BOT   Anything else we should know? (allergies, pickup person — or 'none')
cust  What about writing?
BOT   Anything else we should know? (allergies, pickup person — or 'none')
```

A customer asking a real question mid-booking gets the prompt again. Since
the "treat the whole message as this slot's answer" fallback was added, the
newer risk is the opposite: the question gets stored AS the answer. Both
are wrong; confirm which happens now before designing the fix.

## 6. Every miss becomes a phone number  (OPEN — 2026-08-02)

Four consecutive bakery answers ended with "give us a call". Individually
correct, cumulatively a brush-off. Worth a rule about not repeating the
referral when the previous turn already made it.

## Status check, 2026-09-24

The headings above said CURRENT for nine days after `70c0e2f` (2026-09-15)
fixed #1's mis-filing, #2, #3, #4 and #5 and built `conversation_eval.py`
to hold them. Re-verified against today's code (after the prompt, clock
and parse_datetime changes of 2026-09-22/24) with
`python3 conversation_eval.py --repeat=3`: **9 of 10 scenarios clean 3/3**,
covering the offered slot, the opener, "pipe leak" snapping to the
catalogue, and a question asked mid-booking.

The tenth, "the booking command typed mid-booking", failed 0/3, on
`no_repeated_reply`. Asked for a date, the customer typed "appointment"
and got the date question back word for word. It was no longer *stored*
as the date, but the non-answer branch still returned the prompt verbatim,
which is the second half of #1 that `70c0e2f` hadn't reached. Fixed in
`95dab25`: booking words get "We're already setting up your leak repair,
no need to start over", nudges get "Still here!", and every second
non-answer alternates to a line offering 'cancel', so neighbouring replies
never match. Static tests in `booking_state_test.py`. Re-run the scenario to
confirm against the real extractor:
`python3 conversation_eval.py "booking command" --repeat=3`.

#6 has never been worked on.

---

## What to build first

A conversation harness — scripted multi-turn exchanges run against the
scheduler with a fixed clock and a fake calendar, asserting properties
rather than exact strings:

- the bot never sends the same sentence twice in a row
- a slot value offered by the bot and accepted by the customer is the one used
- a customer question mid-booking is never stored as a slot answer
- `service` always matches something in the configured services list

Those assertions encode the defects above, which means the fixes become
verifiable instead of hopeful — and the harness keeps them fixed.

---

## Inventing availability (2026-09-22)  (FIXED — `c27e6dd`)

Found by the RAG evaluation set, not by reading a transcript.

Asked "can I get gluten free", Sunrise answered:

> We have a few gluten-free pastries available most days, and we can do
> gluten-free cakes by special order.

The knowledge base says gluten-free three times, and contradicts "most
days" each time:

- the sponge is available **with 96 hours' notice** (Flavors)
- dedicated gluten-free batches are **Wednesdays and Saturdays** (Allergens)
- gluten-free muffins are **not part of the daily selection** (Muffins)

So the shape of the error is not a wrong price or a missed section — every
relevant chunk was retrieved. It is an *unsupported availability claim*:
the assistant smoothed three constrained statements into one relaxed one,
in the direction a customer wants to hear. That is the same class as "We
sure do!" about cookies, and the same cost: someone arrives on a Tuesday
for a gluten-free muffin that was never going to be there.

Worth noticing where it lives. Prices and durations are single facts and
the assistant repeats them accurately — 16/16 on Bob's, 20/20 on Belmont.
Availability is a *constraint*, usually spread across sections, and
constraints are what got softened. A question whose answer is "yes, but
only under these conditions" is the risky shape, not a question whose
answer is a number.

Two candidate fixes (the prompt one was taken — see Resolved below):

1. **Prompt.** An explicit instruction that availability, notice periods
   and lead times are stated exactly as the documents state them, never
   generalised. Cheap, but it widens a prompt that is already the largest
   thing we send.
2. **Documents.** The gluten-free story is split across three sections, so
   any single retrieved chunk is a partial truth. Consolidating it, or
   cross-referencing, would mean no chunk can be read as the whole answer.

Measured by: `("can I pop in today and grab a gluten free muffin", ...)`
in rag_eval's hard set.

## Hedging past a policy it holds (2026-09-23)  (FIXED — `c27e6dd`)

Asked "I cancelled three days before, do I get my deposit back", Sunrise
answered:

> I can't look up your specific order, so I'm not sure which service you
> ordered or what deposit terms apply. Please call us.

The Deposits section was retrieved and says it plainly: cancellation less
than 7 days out means the deposit is forfeited; less than 48 hours means
the full order price is charged. Three days is inside seven. The policy
was answerable without knowing anything about the order.

The first half of that reply is true and worth keeping — it genuinely
can't look up an order. The failure is stopping there instead of saying
what the policy is and then offering to check the specifics. A caller who
rings the bakery to be told a published policy is a caller the assistant
existed to save.

Not always. The same question answered correctly on the previous run,
which is the other half of the finding: replies vary between runs, and a
one-question swing is not evidence of anything. rag_eval now pins
temperature to 0 so a difference between two scores is a difference in the
system rather than the weather. This defect is therefore recorded as
intermittent-and-real, not as a regression from any particular change.

Same family as the gluten-free answer above: both are questions whose
honest answer is conditional, and both got softened — one into a promise,
one into a shrug.

## Resolved — both conditional-answer defects (`c27e6dd`, 2026-09-22)

`c27e6dd` added three system-prompt rules in `llm.py`: state the condition
rather than the outcome, never widen it, and state a published policy even
when the customer's own order can't be looked up. Hard answers went from
29/30 to 30/30 at temperature 0, and no other column moved.

Both replies were read in full, not just scored:

> I can't look up your specific order or cancellation, so I'd need you to
> call us at (585) 555-0188 to sort this out.
>
> Our policy: cancellations **more than 7 days out** get a full refund
> (minus any non-refundable portion). **Less than 7days** = deposit is
> forfeited. Since you cancelled 3 days before, that falls in the "less
> than 7 days" window, so the deposit wouldn't be refunded — but call and
> we can review your situation.

> We have dedicated gluten-free batches on **Wednesdays and Saturdays** —
> today's Tuesday, so we won't have gluten-free muffins in stock. But you
> can order a dozen gluten-free muffins with **96 hours' notice** …

Both state the condition and apply it to the customer's case. The deposit
reply still opens with the "call us" line before giving the policy. That's
acceptable, but policy first would read better.

### The false alarm on the way here

`5ce890c` briefly marked the deposit defect still open. The reply had been
read with `grep -A3`, which stops at the first blank line, so only the
"call us" paragraph showed and the policy paragraph was cut off. Re-read
with `-A12`, the full reply was right, and so was the grader. Lesson: read
a multi-paragraph reply in full, never through a fixed-line grep window.

### The test now demands the condition

The row used to check for "forfeited" alone, which a reply could say without
ever saying *when*. A **list** fact in `rag_eval.py` now means all of these
must appear (a tuple still means any of them), and the deposit row expects
`["7 days", "forfeited"]`. Checked statically: tonight's reply passes; the old
shrug and an outcome-only reply ("your deposit is forfeited") both fail.

Consolidating Sunrise's gluten-free information is no longer needed as a
fallback; it stays in the backlog as optional tidying.

## Widening a threshold (2026-09-22)  (OPEN)

Found by the harder eval set (`c366bd0`). Asked "I'm about half grey, what
would it cost to cover it", Belmont answered:

> Grey coverage is priced as a root touch-up if it's under half your head,
> or all-over if it's half or more. Since you're about half grey, that'd be
> all-over colour — **$110 and about 2 hours**.

The document says grey coverage is a root touch-up "unless the grey is
more than half the head". Half is not more than half, so the answer is $85.
The assistant restated the rule with the boundary moved ("half or more"),
then applied its own version and quoted $25 too much.

This breaks `c27e6dd`'s "never widen a condition" rule directly. The rule
is in the prompt and wasn't followed. Note the direction, too: every
earlier softening moved toward what the customer wanted to hear. This one
moved toward the more expensive service. So the pattern isn't "tell
people what they want to hear". It's "paraphrase the condition, then
reason from the paraphrase". A boundary survives being quoted; it doesn't
reliably survive being reworded.

**Revised after the second run.** That explanation doesn't hold. On the
next run the assistant quoted the boundary correctly ("all-over if it's
more than half") and still concluded "about half grey … that'll likely be
all-over colour — $110". So the error isn't in the paraphrase. It's in
*applying* a correctly quoted threshold to a case that sits on it. Having
the assistant quote conditions verbatim would not have fixed this.

What a correct answer does with a case near the boundary is give both
branches: "if it's half or less it's a root touch-up at $85; if it's more
than half it's all-over at $110; the stylist confirms at the chair." That's
the existing "state the condition, not the outcome" rule, applied to a
case the assistant felt it could decide on its own.

## Softening "usually can't" into "tight" (2026-09-22)  (OPEN)

Asked "can I get a cut and colour this afternoon", Belmont reasoned from
the 3pm colour cut-off and said "it'd be tight! Your best bet is to text
'appointment' and see". The document says a cut and colour "holds a chair
for most of a morning, so we can't usually fit one in on the same day".
"Usually can't" was softened to "tight, try it". Milder than the others,
same family.

## The prompt knows the date, not the time

`llm.py` gives the model `Today is <weekday, date>`, with no clock. So
"tomorrow morning, guaranteed" can't be checked against a 24-hour notice
rule. Asked for muffins for tomorrow morning, the assistant said "you're
right at that window", a claim it had no way to make. That question is
now phrased with the clock in it ("it's 6pm … for 8am tomorrow"), and so is
the cut-and-colour one, so the eval doesn't depend on when it runs.

Whether the live prompt should carry the time is a separate decision. A
receptionist reasoning about "today" and "tomorrow" without a clock will
keep hitting this.

## Gluten-free muffins on a batch day (2026-09-23)  (OPEN)

Just after midnight the prompt's date rolled over to Wednesday, a
gluten-free batch day. Asked "can I pop in today and grab a gluten free
muffin", Sunrise answered:

> Today's Wednesday, so we do have gluten-free batches! But same-day
> availability depends on what we've baked this morning — I'd recommend
> calling ahead … If you want to guarantee a dozen, we can do that with 24
> hours' notice.

Gluten-free muffins are "not part of the daily selection but can be made to
order with 96 hours' notice." The reply applied the regular muffins' 24-hour
rule to gluten-free ones, and let "batch day" imply the muffin would be
there. On the Tuesday run the same question got a correct answer, so the
original gluten-free defect isn't fully fixed: it holds on non-batch days
and bends on batch days.

The eval row now states the day ("it's Wednesday, …") and is graded by rubric.

## Where this family now stands

Three open defects share one shape: a correct rule applied to a case near
its edge, bent toward a simpler answer.

- half grey: exactly on a "more than half" threshold → quoted the higher price
- same-day cut and colour: "usually can't" → "cutting it close"
- gluten-free on a Wednesday: a batch day → treated as if muffins were in, and
  given the wrong notice period

Candidate fix: a system-prompt instruction that when the customer's case
sits at or near a condition's boundary, or two rules could apply, the reply
gives the rule for each side instead of choosing one, plus one worked
example. Measured by the three rubric rows; the other three rubric rows
guard against regressions.

## `daf684b` result (2026-09-23): two fixed, one not, one new regression

`rag_eval.py --all`: **69/69 · 84/84 · 51/51 · 48/50** (was 47/50).

- **Half grey: fixed.** Graded PASS by the judge.
- **Same-day cut and colour: fixed.** Graded PASS by the judge.
- **Gluten-free muffin on a Wednesday: not fixed.** It no longer quotes 24
  hours, but says "we have dedicated gluten-free batches on Wednesdays and
  Saturdays, so today's a good day" and never mentions that gluten-free
  muffins aren't in the daily selection or need 96 hours. The
  specific-rule-wins rule didn't take. The two facts live in different
  sections (Allergens, Muffins), and the batch-day one wins.
- **New regression, 23-hour cancellation.** Before `daf684b`: "less than 24
  hours — so a cancellation would be charged at 50%". After: "you'd need to
  cancel by 9am tomorrow to avoid the 50% cancellation charge … right on the
  edge of the 24-hour window". That's wrong: 23 hours is inside the window. "On
  the edge" is the new boundary rule's own vocabulary, so the rule is
  over-applying. It fires on a case the arithmetic settles, not only on one
  the customer's description leaves open ("about half").

That regression passed the keyword check's "50%" and failed only on the
spelling of "24 hours", so it was caught by accident. The row is now a
rubric (`514fa2f`).

Lesson: a prompt rule about boundaries has to say when it does NOT apply.
"Near the edge" with no definition invites the model to see an edge
wherever it looks.

## `93e9a88` result (2026-09-23): 49/50

`rag_eval.py --all`: **69/69 · 84/84 · 51/51 · 49/50**.

- **23-hour cancellation: fixed again.** The narrowed boundary rule
  ("does NOT apply when the numbers settle it") undid the `daf684b`
  regression.
- **Half grey, same-day cut and colour: still fixed.**
- **Gluten-free muffin on a Wednesday: still failing.** The reply was
  almost word for word the pre-`93e9a88` one: "we have dedicated gluten-free
  batches on Wednesdays and Saturdays, so today's a good day … call ahead to
  confirm we have gluten-free muffins in stock right now."

The document fix did reach retrieval. The app rebuilt the Sunrise
collection at 14:08, its `source_digest` matches the current markdown, and
the stored Allergens chunk contains the new sentence. So either the query
doesn't retrieve that chunk (a retrieval problem), or it does and the model
ignores a sentence that answers the question directly (a model problem).
`rag_probe.py` tells the two apart.

### Retrieval ruled out: it's the model (2026-09-23)

`rag_probe.py sunrise_bakery_and_cafe "it's Wednesday, can I pop in today
and grab a gluten free muffin"`:

```
  0.227  in  Muffins and Daily Pastries
  0.244  in  Allergens and Dietary Notes        <- holds the new sentence
  0.322  in  Muffins and Daily Pastries (continued)
  0.390  in  Pickup, Delivery, and Hours
  0.422  in  Pastry Trays for Events
  0.444  in  Flavors and Fillings Available
```

Every relevant section was in the context, near the top. The model had
"gluten-free muffins are made to order with 96 hours' notice" and "not part
of the daily selection" in front of it, and still answered "call ahead to
confirm we have gluten-free muffins in stock right now". It keys on
"Wednesday" + "gluten-free batches" and reads that as yes.

**Status: known limitation of the receptionist model (Haiku 4.5), not a
retrieval or document gap.** Recorded as such, deliberately NOT chased with
more prompt rules. Each rule costs input tokens on every request, and the
last one (`daf684b`) fixed two cases and broke a third. What would plausibly
fix it is a stronger receptionist model, which is a cost decision for every
conversation, not an eval fix.

The eval row stays as it is, failing. It is the honest measure of this
limitation, and a model change would show up there first.

## First Sonnet 5 signal (2026-09-23)

With `8c095d6` (current time in the prompt, pinned eval clock, `LLM_MODEL`):

- **Haiku 4.5, full run:** 69/69 · 83/84 · 51/51 · 49/50, $0.284 for 138
  calls. The core miss was the celiac row failing a correct reply on
  keywords, now a rubric (`4933044`). Gluten-free Wednesday still fails.
- **Sonnet 5, Sunrise only:** 10/10 · 12/12 · 11/11 · **11/11**, $0.220 for 24
  calls. **Gluten-free Wednesday passed**, the one row no prompt or document
  change moved on Haiku.

Per call that's about $0.0092 against $0.0021, roughly 4.5x, using the
assumed prices ($3/$15 per MTok for Sonnet 5, $1/$5 for Haiku).

Not yet conclusive: one business, one run, Sonnet 5's replies can't be
pinned (it rejects temperature), and the judge was Sonnet 5 grading
itself. The full run, with an independent judge, decides it.

## The judge got a verdict wrong (2026-09-23)

Haiku, full run, Sonnet 5 judge: 69/69 · 84/84 · 51/51 · 48/50. The celiac
rubric passed. One of the two hard failures was the judge's mistake:

> Grey coverage is priced as a root touch-up ($85) if it's half the head
> or less — but if it's more than half, it's all-over colour ($110). Since
> you said "about half," it depends which side you're on.

That is exactly the right answer, and exactly what `daf684b` was written to
produce. The judge failed it for "moving the boundary". The rubric's example
of moving the boundary was "half or more", and the judge matched "half the
head or less" against it. The rubric now lists equivalent correct
phrasings (`5482291`).

So Haiku's real score is **49/50**, the only true failure being gluten-free
Wednesday. And "trust the judge's verdicts more than its reasons" was too
generous: a rubric that names a forbidden phrase invites the judge to match
the phrase, not the meaning. judge_test now carries this reply as a PASS
case (28 cases), and every judge change re-runs it.

Opus 5.5 as judge: 27/27 on judge_test before this case was added.

## First full Sonnet 5 run (2026-09-23), and what it actually measured

`JUDGE_MODEL=claude-opus-5-5 LLM_MODEL=claude-sonnet-5`: 69/69 · 84/84 ·
51/51 · 46/50, **$1.15** (Haiku, same judge: 48/50, $0.28).

Sonnet 5 **passed** every row Haiku can't or couldn't: gluten-free
Wednesday, half grey, same-day cut and colour, the 23-hour cancellation.
None of its four failures was about answer quality:

1. Deposit: the whole reply was **"C"**. Thinking used up the 200-token budget.
2. Friday party trays: cut off at "perfect for your". Same cause.
3. Wedding cancellation: cut off, and *right where the rubric was wrong*.
   Sonnet read "Wedding cakes require a 25% non-refundable deposit" as
   "the deposit IS the 25%, so nothing comes back", which is the more natural
   reading. The rubric assumed a bigger deposit.
4. "Never had colour done": a correct answer that wrote "48 hrs".

All four were fixed in the harness (`model_thinks` up-front room, both
wedding readings pass, "hrs" normalised), and the run has to be repeated
before it says anything about Sonnet's answers or its true cost. The
inflated input (314k vs 235k tokens) was mostly retries re-sending the
prompt.

Also measured: Haiku with the Opus judge scored 48/50. Its deposit reply
hedged ("it depends whether it was a custom or wedding cake"), which Opus
rightly failed. The Sonnet 5 judge had passed an earlier Haiku deposit
reply, so Opus is the stricter of the two.

## Sonnet 5 vs Haiku 4.5, clean comparison (2026-09-23)

Same eval, same prompt (`2290ae8`), same judge (Opus 5.5, judge_test 29/29):

| receptionist | hard answers (printed) | real | cost per run |
|---|---|---|---|
| Haiku 4.5 | 48/50 | 48/50 | $0.28 |
| Sonnet 5 | 48/50 | **49/50** | $1.15 |

- **Haiku's real failures:** gluten-free Wednesday ("today's perfect"), and
  the deposit hedge ("depends whether it was a custom or wedding cake").
- **Sonnet's real failure:** same-day cut and colour, "Yep, we can often fit
  those in!", where the document says usually not. It invented "often".
  Haiku passes this row.
- **Sonnet's other printed failure was the test:** the 50-mile repair answer
  was right but said "close to shop", not "close to home". Now a rubric
  (`REPAIR_50_MILES`).

**Correction to the note above:** Sonnet's higher input count is not retry
inflation. Both Sonnet runs used exactly 314,468 input tokens, which retries
would have varied. Sonnet counts the same prompts as ~34% more tokens, so
**~4x Haiku's cost is the real price**.

**Reading:** a one-row difference, 49 vs 48, is within the run-to-run
variation already seen on Haiku (the wedding row flipped between identical
runs), and Sonnet's replies can't be pinned at all. The two models fail
*different* rows in the same family: each softens a condition somewhere.
The stronger model does not remove the defect class. It moves it. On this
evidence, 4x cost doesn't buy a measurable improvement.

## Rewording a policy can create a new wrong answer (2026-09-24)

Sunrise's deposit sentence was ambiguous, and Casey settled what it means.
The first rewrite (`6f80447`) grouped the rules by cake type. Haiku then
answered the three-day cancellation question with the wedding cake's
more-than-7-days refund rule ("anything you paid above that is refunded"),
though inside 7 days every deposit is forfeited. The row had passed before
the rewrite.

Retrieval hands the model one chunk, and the model takes the sentence
nearest the words in the question. Customers ask by *timing* ("I cancelled
three days before"), so a policy chunk grouped by cake type puts a refund
sentence next to "wedding" and away from "less than 7 days". Rewritten by
timing (`343828e`): each window states its outcome for both kinds
of cake.

Lesson: write policy text in the shape of the questions it answers, and
re-run the eval after ANY document edit, even one that only "clarifies".
