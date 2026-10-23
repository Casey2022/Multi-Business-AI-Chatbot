# Bookings check notice periods: design

Decided 2026-10-20. Status: version 1 built the same day (tables for
Sunrise and Crosstown; the booking flow refuses and filters), then the
duplication removed: generated knowledge-base lines (notice first, before
prices: after them, "Friday at five" offered trays 2 runs in 9), and
`check_notice` reading the table as a floor. Version 2 (2026-10-22):
variants and an owner page, below.

## The gap

The booking flow never looks at notice: there is no "notice" anywhere in
`scheduler.py`. Journey "it's 6pm, muffins guaranteed for 8am" (Sunrise, a
dozen needs 24 hours) starts an order, 0/3. The alternatives it offers can be
inside the notice too: the Thanksgiving journey offered today at 2:30pm.

The numbers live only in knowledge-base prose ("We need at least 72 hours'
notice for any custom cake order"). The Q&A path gets by because the model
reads the number from the retrieved excerpt and passes it to `check_notice`
(`notice_hours`). The booking flow has no excerpt and no model step to read
one, so it has nothing to check against.

## The rules there are (seed documents, 2026-10-20)

| Business | Rule | Shape | Version |
|---|---|---|---|
| Sunrise | custom birthday cakes 72h; wedding cakes 2 weeks; cupcake orders 24h; pastry trays 48h; muffins by the dozen 24h | flat, per service | 1 |
| Crosstown | party trays 3h, and Fri/Sat ordered by 4pm | flat + weekday cutoff | 1 |
| Sunrise | themed cupcake displays 72h; gluten-free 96h (cakes and muffins) | a variant, triggered by words | 2 |
| Belmont | new colour clients: patch test 48h before | a precondition, not notice | not this |
| Sunrise | wedding cakes "ideally 4-6 weeks in peak season" | advice | stays prose |

## Decisions

1. **Where it lives: config, keyed by service**, the way cancellation rules
   moved to `policies.cancellation` and prices to `menu:`. One source of
   truth: the knowledge base stops stating the numbers, and each section
   gets a generated notice line (as `menu.price_lines_for` does for prices),
   so retrieval and Q&A still see them.
2. **Too soon: refuse, and offer the earliest time that meets it**, like a
   closed time. "Custom cakes need 72 hours' notice, so the earliest is
   Friday at 10am." The alternatives offered must meet the notice too.
3. **Version 1: flat hours per service, plus weekday cutoffs.** Word-
   triggered variants (themed, gluten-free) are version 2, matched the way
   `policy.kind_from_words` matches cancellation kinds.

## Shape (version 1)

```yaml
booking:
  notice:
    # Hours before the booked time. A service not listed needs no notice.
    custom birthday cakes: {hours: 72}
    wedding cakes: {hours: 336}            # 2 weeks
    cupcake orders (dozen minimum): {hours: 24}
    pastry trays for events: {hours: 48}
    muffins by the dozen: {hours: 24}
```

Crosstown's cutoff, as `check_notice` already takes it:

```yaml
    party trays: {hours: 3, order_by: "16:00", order_by_days: [friday, saturday]}
```

Keys match `services:` by `scheduling.same_service`, so a name that drifts
(an owner renames a service) is reported at startup, as
`unknown_duration_services` already does for durations.

## Readers

- `policy.notice_for(service, config)`: the one lookup. The booking flow
  and `check_notice` both use it.
- The booking flow: when a time is chosen (`_check_datetime_now`) and in the
  alternatives it offers (`scheduling` slot search: earliest = now + notice,
  and not after a cutoff).
- `check_notice`: when the question names a service with a notice entry,
  code fills `notice_hours` / `order_by` from the table, overriding what
  the model read (the `arguments_from_words` pattern).
- The prompt and retrieval: the generated notice line in each section.

## How it's measured

Before: journey "it's 6pm, muffins guaranteed for 8am" 0/3; add a custom
cake booked for tomorrow (72h needed) and a Crosstown tray at 5pm on a
Friday. After: each refused with the earliest time; the alternatives all
meet the notice; every existing booking journey still clean, x3. Mutation-
test the lookup and the alternatives filter.

## Version 2 (2026-10-22): variants and an owner page

**Variants.** An entry can list `variants:`, each `{words, hours, name,
sections}`. `policy.notice_for(service, config, details)` takes the order's
own words and returns the longest notice they name (with `variant`: its
name, for the refusal). The booking flow's words are `scheduler._order_details`:
the customer's opening message (`_asked`, kept because the extractor files
"a dozen gluten-free muffins" as "muffins by the dozen") plus every answer,
so a "gluten free" given at the notes question is caught by the check
before the read-back. `details` travels with `service` through
`calendar_sync` → backend → `scheduling`. The generated line names each
variant, and a variant's `sections` get a line of their own (gluten-free
sponge under Flavors, gluten-free muffins under Allergens). The prose no
longer states 96 or 72.

Before: three journeys, 0/3 each (gluten-free muffins Thursday, a themed
display Thursday, gluten-free said at the notes question). After: 3/3.

Not matched: a negation ("not gluten free") still names the variant, so
it can refuse a time it didn't need to; the refusal names the rule, so the
customer can say so. Say if it happens.

**Owner page** (`notice_form.py`, `/admin/business/<id>/notice`, linked
from Settings and from each generated line on the Knowledge page). A card
per service: hours or days, an order-by time and its days, each variant's
hours, and the section that shows the line. A draft like the cancellation
rules (`notice_draft` → `notice` on Publish); Publish rebuilds the index
with it, since the lines are in the chunks. Which words name a service or
a variant stay the operator's (copied through untouched). A variant
shorter than its service is refused (it would never apply). Not yet:
adding a new variant from the page.
