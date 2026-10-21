# Design

## 1. Architecture Overview

Six services. **postgres** is the source of truth for invoices and the outbox.
**redis** does three separate jobs on one instance — Celery broker, rate-limiter
counter state, ERP result cache — and I've tried to keep those conceptually
separate even though it's literally one container. **mock-erp** is the external
system of record: hostile, rate-limited, chaotic on purpose, and not something
I get to change. **django-api** handles ingestion, reporting, and health.
**celery-worker** does the actual reconciliation. **celery-beat** dispatches the
outbox, ticking every 2s.

```
Client
  |  POST /api/invoices
  v
[django-api] --validate_invoice()--> invalid --> 400, nothing written
  |
  | valid: one atomic transaction
  v
[Postgres] Invoice(PENDING) + OutboxEvent(PENDING)
  |
  | celery-beat polls PENDING events every 2s
  v
[celery-beat] --reconcile_invoice.delay()--> [Redis: Celery broker]
  |             marks OutboxEvent DISPATCHED
  v
[celery-worker: reconcile_invoice]
  |
  | _claim_invoice(): SELECT FOR UPDATE, PENDING/RETRYING -> PROCESSING
  v
  check [Redis: ERP cache] --hit--> use cached response ------------------+
  |miss                                                                   |
  v                                                                       |
  acquire [Redis: Lua rate limiter] (global 10/sec, atomic)               |
  |denied --> status=RETRYING, retry countdown=1s                        |
  |granted                                                                |
  v                                                                       |
  [mock-erp] (5s client timeout; 200-900ms latency; ~5% 503; ~1% timeout) |
  |transient --> exp. backoff+jitter retry, up to MAX_RETRIES=5           |
  |         --> exhausted --> DEAD_LETTERED                              |
  |permanent (4xx != 429) --> DEAD_LETTERED immediately                  |
  |success --> cache result (TTL 300s) -------------------------------->-+
  v
[Postgres] Invoice.status = MATCHED / UNMATCHED
  |
  v
GET /api/reports/summary <- live indexed queries (status, (status, updated_at))
```

## 2. Scaling to Production Volume

The real ceiling on this whole system is the ERP's global 10 req/sec limit — not
our code, not Postgres, not Redis. At 10 req/sec sustained (200-900ms latency per
call, worst case being mostly cache misses), that's a throughput ceiling of
~36,000/hour, ~864,000/day. That comfortably covers 300k/month growth on its own,
before caching even helps. What it can't do is absorb a 4,000/min burst (66/sec)
directly against the ERP — that has to queue somewhere.

I put the queue in Postgres, as PENDING `OutboxEvent`/`Invoice` rows, rather than
in Redis/Celery. Postgres rows are durable and I can just query them to see what's
backed up; a backlog sitting only in Redis/Celery during a 66-vs-10/sec mismatch
would be harder to inspect and wouldn't survive a restart. Backlog depth during a
burst works out to roughly `(66 - 10) * burst_duration_seconds` rows sitting
PENDING, draining at 10/sec once the burst is over.

`CELERY_WORKER_CONCURRENCY=5` because 5 workers at ~2 req/sec each lands right
around the 10/sec ceiling with some headroom for cache hits. Real invoices
reference POs at roughly 3:1 (the harness generates about one PO per 3 invoices),
so cache hits pull actual ERP load meaningfully below the raw invoice rate.

At 12M+ historical rows, `/reports/summary`'s per-status `.count()` queries still
stay fast thanks to the `status` and `(status, updated_at)` indexes. Past that
scale I'd move to precomputed rollup counters — updated incrementally by the
worker in the same transaction as each status change — instead of counting live
on every request. Didn't build that here; it's the scaling path, not a current
need (see section 6).

## 3. Delivery & Correctness Semantics

Task delivery is at-least-once — that's Celery's own guarantee, and the outbox
adds a second at-least-once layer on top of it for crash-safety between the DB
commit and the task actually getting enqueued. What I'm actually guaranteeing is
exactly-once *persisted outcome*, not exactly-once execution: the ERP might get
called more than once in edge cases, but the final invoice status only ever lands
in one place, correctly. Two mechanisms carry this, and both got tested under
real concurrent load:

- `_claim_invoice()` uses `SELECT FOR UPDATE` inside a short transaction to
  atomically claim an invoice before any processing starts. I tested this with
  20 threads racing a synchronized `threading.Barrier` against the same
  `invoice_id`: exactly 1 claim succeeded, the other 19 correctly saw the
  already-claimed row and backed off (`test_idempotency.py`, Part A).
- the `unique_together` constraint on `(vendor_id, invoice_number, po_number,
  amount, currency)` stops duplicate `Invoice` rows at the database level, so
  exact and near-duplicate submissions of the same logical invoice collapse to
  one row. Ran this against a 2,000-line real sample from the generator: 1828
  inserted, 105 correctly rejected as duplicates.

Where this could still slip: Postgres itself losing a committed transaction
(outside anything this system controls), or a bug somewhere else that bypassed
`_claim_invoice`'s guard entirely. That guard is the one thing the whole
guarantee rests on, which is why it got more concurrency testing than anything
else in this build.

One thing worth flagging because I actually went and checked the code instead of
assuming: `_claim_invoice` only treats `PENDING` and `RETRYING` as claimable —
`if invoice.status not in (InvoiceStatus.PENDING, InvoiceStatus.RETRYING): return None`.
So if a worker dies after claiming (status already flipped to `PROCESSING`) but
before it finishes, that invoice just sits in `PROCESSING` with a stale
`updated_at` — nothing reclaims it. `/reports/summary`'s `processing_over_5min`
counter will surface it to an operator, but there's no automatic re-claim for a
stuck `PROCESSING` row (something like "treat `PROCESSING` older than N minutes
as claimable too" isn't built).

## 4. Failure Modes

| Scenario | Behavior |
|---|---|
| ERP down for an hour | All calls timeout/503 → classified transient → exponential backoff, exhausted after `MAX_RETRIES=5` → `DEAD_LETTERED` with reason captured. No infinite loop. New invoices reconcile normally once the ERP recovers; already-dead-lettered ones need manual reprocessing — no automatic re-drive built. |
| Redis restarts | The cache side fails open cleanly — `cache.py` catches `redis.exceptions.RedisError` on both read and write, logs a warning, and returns `None`/no-ops, so it just falls through to a real ERP call instead of crashing. The rate limiter doesn't have that same protection, and I checked `rate_limiter.py` directly to confirm: `RateLimiter.acquire()` has no try/except around the Lua script call, and the call site in `reconcile_invoice` (`if not limiter.acquire():`) doesn't wrap it either. A `RedisError` there raises uncaught, outside the `try/except` that only covers the `call_erp()` step — so `classify_error`/retry logic never even sees it. There's no `autoretry_for` configured on the task, so Celery just marks it `FAILURE` with no retry, and the invoice — already flipped to `PROCESSING` by `_claim_invoice` — is stuck the same way as the worker-crash case below, with no automatic way back. Same underlying gap as section 3. |
| Worker dies mid-invoice | Covered above — `PROCESSING` stuck, surfaced via reporting, not auto-healed. |
| Poison message (malformed data that always errors) | Classified permanent → straight to `DEAD_LETTERED`, never loops. |
| Reporting query degrades at scale | Noted in section 2 — materialized rollups are the path, not yet built. |

## 5. Caching Strategy & Invalidation

TTL-only, 300s, no active invalidation — the ERP doesn't give me any
change-notification mechanism to invalidate against in this harness. The cache
key is built from `(vendor_id, po_number, amount, currency)`, with `amount`
quantized to 2 decimal places before it gets stringified, because
`Decimal('1000.0')` and `Decimal('1000.00')` are the same value but different
strings and would otherwise produce false cache misses. That was an actual bug I
caught and fixed while building this, and it's what
`test_cache_key_normalizes_decimal_representation` verifies.

There's a stale-match risk — a PO could change in the ERP mid-TTL-window — and
I'm accepting it, bounded to 5 minutes of maximum staleness. As for hot keys: a
single frequently-referenced PO (real invoices hit POs at roughly 3:1) is exactly
what makes caching worth doing here, and Redis handles concurrent reads of one
key without any trouble.

## 6. Reporting at Scale

Right now this is six live indexed `COUNT` queries, one per status, rather than
a single `.values('status').annotate(count=Count('id'))` group-by — simpler to
reason about, and it correctly returns zero for statuses with no rows without
any extra handling. At 12M+ rows I'd move this to materialized counters — one row
per status, incremented/decremented in the same transaction as each status
change — or periodic rollup tables refreshed by a scheduled job, trading
real-time-exact for near-real-time-approximate. Didn't need to build that for
this slice; the sample dataset doesn't push it that far.

## 7. Observability & On-Call

Nine metrics live in `observability.py`: `invoices_ingested_total{validation}`,
`invoices_processed_total{status}`, `invoices_dead_lettered_total{reason}`,
`erp_requests_total{outcome}`, `erp_request_latency_seconds`, `erp_retries_total{reason}`,
`cache_hits_total`, `cache_misses_total`, `celery_queue_depth` (defined, never
populated — see Known Gaps).

What I'd actually page on:
- `erp_requests_total{outcome!=success}` spiking — upstream's degrading.
- `invoices_dead_lettered_total` spiking — something's systematically broken,
  not just noise.
- `stuck.retrying_over_5min` or `processing_over_5min` (from
  `/reports/summary`) crossing a threshold — backlog forming, or workers
  dying.

First dashboard I'd build: status totals, match-rate trend, and these three
alert conditions on one screen — which is basically just what
`/reports/summary` already returns.

## 8. Tradeoffs

See [TRADEOFFS.md](TRADEOFFS.md) for the three biggest decisions and what they cost.

## Harness Observation

Running the pipeline end-to-end against a real 2,000-invoice sample, the match rate came back near zero (0 MATCHED out of 476 completed invoices) — far below what `generate_invoices.py`'s own comments imply ("the rest are clean and should reconcile against the mock ERP"). I read both scripts directly to find out why: `generate_invoices.py` assigns each invoice a random `vendor_id` and `amount` independently of any real PO data, and `mock_erp.py` separately builds its own PO table using a different RNG seed derivation (its own comment says "different stream than the generator's invoices"). Since the two sides never share the vendor/amount values for a given PO number, a "clean" invoice's vendor and amount only match the ERP's record for that PO by chance — roughly a 1-in-10 vendor match times a ~1% amount-tolerance match, well under 1% combined, not the ~81% the harness's comments describe. This is a data-generation inconsistency in the provided harness, not a bug in the reconciliation pipeline — `UNMATCHED` is a valid, correctly-classified outcome, and the pipeline correctly distinguishes `po_not_found`, `vendor_mismatch`, and `amount_out_of_tolerance` reasons regardless of how often a true match occurs.
