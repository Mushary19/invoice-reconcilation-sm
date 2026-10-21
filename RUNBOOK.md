# Runbook

Copy-pasteable commands for a live walkthrough, in order. See README.md for prose docs.

## 0. Fresh Start (if I need to reset everything)

```bash
docker compose down -v
cp .env.example .env
docker compose up -d --build
```

## 1. Confirm Everything Is Healthy

Sets the baseline — nothing else in this demo means anything if these aren't green.

```bash
docker compose ps
curl http://localhost:8000/api/healthz
curl http://localhost:8000/api/readyz
```

## 2. Generate Sample Invoices

Small batch, fast enough to demo live — not the full 100k.

```bash
python harness/generate_invoices.py --count 2000 --seed 42 --out /tmp/invoices_demo.ndjson
wc -l /tmp/invoices_demo.ndjson
```

## 3. Copy Into Container and Ingest

Shows the atomic Invoice+OutboxEvent write and inline validation happening on real data, not a toy example.

```bash
docker cp /tmp/invoices_demo.ndjson $(docker compose ps -q django-api):/app/invoices_demo.ndjson
docker compose exec django-api python manage.py ingest_invoices invoices_demo.ndjson
```
vv
## 4. Watch Reconciliation Happen Live

Shows the outbox dispatch -> claim -> rate limit -> ERP call -> status flip pipeline actually running, not just described.

```bash
docker compose logs -f celery-worker
```
(Ctrl+C to stop watching, does not stop the container)

## 5. Check Results

Live indexed report + the actual Prometheus metrics an operator would page on.

```bash
curl http://localhost:8000/api/reports/summary | python3 -m json.tool
curl http://localhost:8000/api/metrics | grep -E "erp_requests_total|invoices_processed_total|cache_hits_total|invoices_dead_lettered_total"
```

## 6. Run the Test Suite

The full suite, real Postgres + Redis, no mocks at the infra layer.

```bash
docker compose exec django-api pytest reconciliation/tests/ -v
```

## 7. Demonstrate Idempotency Live

Proves idempotency under real duplicate submission, not just unit tests — re-ingesting the SAME file should show duplicates rejected, not double-processed.

```bash
docker compose exec django-api python manage.py ingest_invoices invoices_demo.ndjson
```

## 8. Demonstrate Rate Limiting / Concurrency Tests Specifically

The three tests that force real race windows under real Postgres locking and real Redis — the ones that actually prove the concurrency guarantees, not just assert them.

```bash
docker compose exec django-api pytest reconciliation/tests/test_rate_limiter.py reconciliation/tests/test_idempotency.py reconciliation/tests/test_outbox.py -v
```

## 9. Show a Single Invoice End-to-End via API

A single request, watchable start to finish — ingest, dispatch, reconcile, report.

```bash
curl -X POST http://localhost:8000/api/invoices \
  -H "Content-Type: application/json" \
  -d '{"vendor_id": "V-1001", "invoice_number": "1001-99999", "po_number": "PO-000001", "amount": 1000.00, "currency": "USD", "source": "demo"}'
```

Same invoice, same identity fields -> 409, not a new row -> the unique_together constraint working live, not just in a test.

```bash
curl -X POST http://localhost:8000/api/invoices \
  -H "Content-Type: application/json" \
  -d '{"vendor_id": "V-1001", "invoice_number": "1001-99999", "po_number": "PO-000001", "amount": 1000.00, "currency": "USD", "source": "demo"}'
```

## 10. Tear Down (after the call)

```bash
docker compose down -v
```
