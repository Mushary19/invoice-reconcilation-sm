# Tradeoffs

Three biggest decisions in this build.

## 1. Transactional outbox pattern

I added this on top of the original minimal slice. Without it, there's a window
between "Invoice committed" and "task actually enqueued" that a crash or a
Redis-unavailable moment can fall into — the invoice sits there and nothing ever
picks it up. The outbox closes that gap: `Invoice` and `OutboxEvent` get written
in the same transaction, so if the process dies right after, the event is still
there for the dispatcher to find.

It costs an extra table, a dispatcher task, and a 2-second dispatch latency floor
on every invoice, since that's how often celery-beat polls. With more time I'd
want a dedicated one-shot migrate service (see #3) to make startup ordering
actually rigorous — the outbox only protects the DB-commit-to-enqueue gap, it
doesn't protect against `celery-worker` starting up and pulling dispatched tasks
before the tables it needs even exist.

## 2. Per-record transactions in `ingest_invoices.py`, not `bulk_create`

`bulk_create`'s `ignore_conflicts` doesn't play well with the atomic
Invoice+OutboxEvent pairing the outbox needs — a batch-level duplicate could
silently skip `OutboxEvent` creation for the wrong invoice, or for reasons that
are genuinely hard to reason about after the fact. So each record gets its own
transaction instead. The obvious cost is speed: 100k records means 100k
individual transactions, not one batched operation. I'm fine with that tradeoff
here because the ERP's 10/sec limit dominates total pipeline throughput
regardless of how fast ingestion runs — it was never going to be the
bottleneck. If it ever did become one, a two-phase bulk-insert-then-bulk-outbox
approach with careful conflict handling could claw some of that speed back.

## 3. `celery-worker`/`celery-beat` depend on `django-api` via `service_started`

This is the one I'm least happy with. `service_started` only means the
container launched, not that migrations finished — so there's a real, narrow
race window where a worker could start pulling tasks against tables that don't
exist yet, if Postgres is slow to come up or migrations take longer than usual.
It didn't happen in any test run I did, but nothing structurally rules it out
either. I went with it anyway because it was the simplest thing that worked in
the time I had, over `service_healthy` or a proper migrate-then-signal setup.
The textbook fix is a dedicated one-shot `migrate` service with
`condition: service_completed_successfully` that all three app containers
depend on, instead of piggybacking on `django-api`'s own startup command to run
migrate as a side effect.
