# Bookings check notice periods: design

Decided 2026-10-20. Status: version 1 built the same day (tables for
Sunrise and Crosstown; the booking flow refuses and filters). Not yet:
the generated knowledge-base lines, `check_notice` reading the table, and
variants (see the backlog).

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
