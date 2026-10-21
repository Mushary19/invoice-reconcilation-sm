# Invoice Ingestion & Reconciliation Pipeline

See [DESIGN.md](DESIGN.md) for architecture and [TRADEOFFS.md](TRADEOFFS.md) for the biggest decisions made under time pressure.

## Setup

```bash
docker-compose up -d --build
```

Wait for postgres/redis/mock-erp to report healthy via `docker-compose ps` before using the API.

## Ingesting Sample Data

```bash
# from harness/, outside docker -- the generator is stdlib-only
python generate_invoices.py --count 100000 --seed 42 --out invoices.ndjson

docker cp invoices.ndjson $(docker compose ps -q django-api):/app/invoices.ndjson
docker compose exec django-api python manage.py ingest_invoices invoices.ndjson
```

## What's Built

Ingestion API with inline validation, transactional outbox, Celery-based reconciliation with global rate limiting (Redis Lua script), retry with exponential backoff + jitter, dead-lettering, TTL caching, reporting endpoint, health/readiness checks, Prometheus metrics endpoint, structured JSON logging.

## What's Deferred (per assignment scope)

Auth/multi-tenancy, UI, real payment integration, processing all 12M production-scale records (the sample dataset proves the design, per the assignment's own instructions).

## Known Gaps

- A stale `PROCESSING` invoice (worker died mid-reconcile) is not automatically reclaimable — `_claim_invoice` only treats `PENDING`/`RETRYING` as claimable. Surfaced via `/reports/summary`'s `processing_over_5min`, not auto-healed. See DESIGN.md §3.
- `rate_limiter.py`'s `RateLimiter.acquire()` has no Redis-down protection, unlike `cache.py`. A Redis outage during the rate-limit step raises uncaught, bypasses retry/dead-letter classification entirely, and leaves the invoice stuck in `PROCESSING` (compounds with the gap above). See DESIGN.md §4.
- `celery-worker`/`celery-beat` depend on `django-api` via `service_started`, not a guarantee that migrations have finished — a narrow startup race window. See TRADEOFFS.md §3.
- `celery_queue_depth` metric is defined but never populated — no queue-length query mechanism decided yet.
- Celery's `SoftTimeLimitExceeded` is not explicitly caught in `reconcile_invoice`.
- Cache staleness is bounded at 5 minutes (TTL-only), not event-driven invalidation — the ERP exposes no change-notification mechanism to invalidate against.

## Running Tests

Tests run inside the docker-compose network. This is the only supported path — there is no bare-metal/local-venv test setup.

```bash
docker-compose up -d --build
docker-compose exec django-api pytest reconciliation/tests/ -v
```

Wait for all services to report healthy (check with `docker-compose ps`) before running tests — particularly Postgres and Redis, since the test suite depends on both being reachable.
