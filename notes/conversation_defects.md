# Conversation quality — defects found in the transcripts (2026-09-15)

Method: 75 conversations / 700 messages pulled from the `messages` table and
read in conversation order, rather than recalled. Each defect below cites a
real exchange and a date, so "still broken" is a fact rather than a guess.

---

## 1. A slot is re-asked with the identical sentence  (CURRENT — 2026-09-15)

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

## 2. Service name is whatever sentence happened to land  (CURRENT)

The appointment from that conversation is titled **"I have a leaky pipe"**.
It reaches the calendar and the owner's list that way. `service` is free
text with no normalisation against the business's configured services.

## 3. An offered slot is not honoured when the customer accepts it  (CURRENT — 2026-09-15)

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

## 4. Stacked, ungrammatical greeting  (CURRENT)

```
cust  I'd like to book an appointment for next wednesday
BOT   Happy to help with for next wednesday. Happy to schedule an
      appointment! What service do you need?
```

`_ask_next_or_finalize`'s first-turn acknowledgement builds "Happy to help
with {known}" where known is "for next wednesday", then appends the
greeting, which is itself a greeting. Two hellos and a broken preposition.

## 5. Mid-booking questions are not answerable  (2026-08-02, needs re-checking)

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

## 6. Every miss becomes a phone number  (2026-08-02)

Four consecutive bakery answers ended with "give us a call". Individually
correct, cumulatively a brush-off. Worth a rule about not repeating the
referral when the previous turn already made it.

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
