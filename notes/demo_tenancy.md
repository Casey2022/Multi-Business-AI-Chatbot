# Demo tenancy — how the sandbox works

A visitor to `/demo` picks a business and gets **their own copy of it**: a
real tenant in the businesses table, cloned from a template, deleted when
they stop using it. Not a shared demo account, and not a read-only tour.

## Why a clone per visitor

A demo that shows a real client's portal lets a stranger edit that client's
settings. A single shared demo business lets two visitors overwrite each
other's edits mid-sentence. Cloning is the only version where a visitor can
*change* things — which is the whole point, because the product isn't the
chat, it's the portal behind it.

The clone is cheap because a business is just a row plus its overrides plus
its documents. The one expensive part is the vector collection.

## The shared collection, and the one place it's dangerous

A fresh clone doesn't get its own Chroma collection — it reads the
template's. Two readers, one library card. That saves an embedding call per
section per visitor and costs nothing, *until* a visitor presses Publish:
re-ingestion rebuilds the collection named on the config, so a demo still
sharing would rebuild the real client's knowledge base out of the visitor's
edited documents. The client's bot would then answer confidently from a
stranger's text, with nothing in any log saying why.

`demo.fork_collection()` is the guard. Every path that re-ingests calls it
first; it's a no-op for a real business, and for a demo still sharing it
repoints the row at a collection named after its own slug. It must run
**before** `load_config`, because `load_config` is what stamps the
collection name onto the config — the same ordering trap as writing into
`pending` after `set_state`.

## Which collection a business reads from is a column

`businesses.rag_collection`, not the slug and definitely not the name.
Deriving the name can't express "these two share one deliberately", and
the name is owner-editable — that bug already cost us once.

## Isolation, in the order it matters

1. **The calendar.** Both templates carry a live Google `calendar_id`, and a
   clone shares the YAML file. `load_config` forces `calendar.provider =
   "simulated"` for any demo, stamped after the overrides where nothing
   typed can reach it. Without this a visitor's pretend 9am booking lands in
   a real business's week, and their cancellation deletes a real job.
2. **The knowledge base.** See fork_collection above.
3. **Data.** Messages, appointments, booking state and settings are all
   scoped by `business_id` already; the clone gets a `business_id` and
   inherits every boundary the multi-tenancy work built.
4. **The portal.** The demo user is an owner scoped to the clone, so
   `require_business_access` 403s them out of Bob's and Sunrise.

## Teardown fails closed

`db.delete_business()` refuses anything not flagged `is_demo` unless forced,
and nothing in the sweep forces. Refusing wrongly leaves a stale row;
deleting wrongly destroys a client's whole history. Not the same size of
mistake, so the code doesn't treat them as one. Teardown also drops a
collection only when its name matches the demo's own slug.

`_TENANT_TABLES` in db.py lists every business-scoped table explicitly
rather than discovering them, so adding a scoped table without thinking
about teardown shows up as a review question there.

## Three limits, not one

- **Rate** — `ratelimit.demo_start` caps how fast one address can mint them.
- **Total** — `demo.MAX_LIVE` caps how many exist at once; a full house
  sweeps first and only then refuses.
- **Time** — `demo.sweep_idle` clears demos nobody has touched in
  `DEMO_IDLE_MINUTES`, and `sweep_all` runs at boot, because a demo that
  outlived the process has a visitor whose browser session is long gone.

The sweep is opportunistic (it runs when someone asks for a demo) rather
than on a timer. There's no scheduler in this app, and a background thread
that deletes rows is a much larger promise than this needs. A late sweep
costs a row that lives too long — an affordable failure.

## Env knobs

`DEMO_IDLE_MINUTES` (60), `DEMO_MAX_LIVE` (50), `DEMO_PER_MINUTE` (3),
`DEMO_PER_HOUR` (20).

## Tests

`demo_test.py` — 46 checks against a temp SQLite file: cloning, what the
visitor starts with, what they can't reach, listing, forking, the refusals,
the ceiling, teardown cascade, and which collection teardown is allowed to
delete. The collection guard was verified by deleting it and watching two
checks go red.
