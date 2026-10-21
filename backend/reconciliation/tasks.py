# Celery tasks: reconcile_invoice, dispatch_outbox_events
import time

import redis
import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .cache import get_cached_match, set_cached_match
from .erp_client import call_erp
from .models import Invoice, InvoiceStatus, OutboxEvent
from .observability import (
    cache_hits_total,
    cache_misses_total,
    erp_request_latency_seconds,
    erp_requests_total,
    erp_retries_total,
    invoices_dead_lettered_total,
    invoices_processed_total,
    log_event,
    new_trace_id,
)
from .rate_limiter import get_rate_limiter
from .retry_policy import (
    MAX_RETRIES,
    PermanentError,
    TransientError,
    backoff_seconds,
    classify_error,
)


def _erp_outcome_label(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.exceptions.HTTPError):
        status_code = (
            exc.response.status_code if exc.response is not None else "unknown"
        )
        return str(status_code)
    return "unknown_error"


def _claim_invoice(invoice_id):

    with transaction.atomic():
        try:
            invoice = Invoice.objects.select_for_update().get(id=invoice_id)
        except Invoice.DoesNotExist:
            return None
        if invoice.status not in (InvoiceStatus.PENDING, InvoiceStatus.RETRYING):
            return None
        invoice.status = InvoiceStatus.PROCESSING
        invoice.save(update_fields=["status", "updated_at"])
        return invoice


@shared_task(bind=True, max_retries=MAX_RETRIES)
def reconcile_invoice(self, invoice_id):
    invoice = _claim_invoice(invoice_id)
    if invoice is None:
        log_event(invoice_id, None, "skip_not_claimable")
        return

    trace_id = new_trace_id(invoice.invoice_id)
    log_event(
        invoice.invoice_id, trace_id, "reconcile_start", attempt=self.request.retries
    )

    redis_client = redis.Redis.from_url(settings.REDIS_URL)

    cached = get_cached_match(
        redis_client,
        invoice.vendor_id,
        invoice.po_number,
        invoice.amount,
        invoice.currency,
    )

    if cached is not None:
        log_event(invoice.invoice_id, trace_id, "cache_hit")
        cache_hits_total.inc()
        response = cached
    else:
        log_event(invoice.invoice_id, trace_id, "cache_miss")
        cache_misses_total.inc()

        limiter = get_rate_limiter()
        if not limiter.acquire():
            log_event(invoice.invoice_id, trace_id, "rate_limit_denied")
            erp_retries_total.labels(reason="rate_limit_denied").inc()
            invoice.status = InvoiceStatus.RETRYING
            invoice.save()
            raise self.retry(countdown=1)

        start = time.time()
        try:
            response = call_erp(
                invoice.vendor_id, invoice.po_number, invoice.amount, invoice.currency
            )
        except Exception as exc:
            error_class = classify_error(exc)
            outcome_label = _erp_outcome_label(exc)
            erp_requests_total.labels(outcome=outcome_label).inc()
            erp_retries_total.labels(reason=outcome_label).inc()

            if error_class is TransientError:
                if self.request.retries >= MAX_RETRIES:
                    invoice.status = InvoiceStatus.DEAD_LETTERED
                    invoice.last_error = f"max_retries_exceeded: {str(exc)}"
                    invoice.save()
                    invoices_dead_lettered_total.labels(
                        reason="max_retries_exceeded"
                    ).inc()
                    log_event(
                        invoice.invoice_id,
                        trace_id,
                        "dead_lettered",
                        reason="max_retries_exceeded",
                        retry_count=self.request.retries,
                        last_error=str(exc),
                    )
                    return

                invoice.status = InvoiceStatus.RETRYING
                invoice.retry_count = self.request.retries + 1
                invoice.last_error = str(exc)
                invoice.save()
                countdown = backoff_seconds(self.request.retries)
                log_event(
                    invoice.invoice_id,
                    trace_id,
                    "retry_scheduled",
                    attempt=self.request.retries,
                    countdown=countdown,
                    error=str(exc),
                )
                raise self.retry(exc=exc, countdown=countdown)

            elif error_class is PermanentError:
                invoice.status = InvoiceStatus.DEAD_LETTERED
                invoice.last_error = str(exc)
                invoice.save()
                invoices_dead_lettered_total.labels(reason="permanent_error").inc()
                log_event(
                    invoice.invoice_id,
                    trace_id,
                    "dead_lettered",
                    reason="permanent_error",
                    last_error=str(exc),
                )
                return

        elapsed = time.time() - start
        erp_request_latency_seconds.observe(elapsed)
        erp_requests_total.labels(outcome="success").inc()
        log_event(
            invoice.invoice_id,
            trace_id,
            "erp_response",
            latency_ms=int(elapsed * 1000),
            matched=response.get("matched"),
            cache_hit=False,
        )
        set_cached_match(
            redis_client,
            invoice.vendor_id,
            invoice.po_number,
            invoice.amount,
            invoice.currency,
            response,
        )

    if response.get("matched"):
        invoice.status = InvoiceStatus.MATCHED
        invoices_processed_total.labels(status="matched").inc()
    else:
        invoice.status = InvoiceStatus.UNMATCHED
        invoices_processed_total.labels(status="unmatched").inc()
    invoice.erp_response = response
    invoice.save()
    log_event(
        invoice.invoice_id, trace_id, "reconcile_complete", final_status=invoice.status
    )
    return


@shared_task
def dispatch_outbox_events():
    events = list(OutboxEvent.objects.filter(status="PENDING")[:500])

    dispatched = []
    for event in events:
        try:
            reconcile_invoice.delay(event.payload["invoice_id"])
        except Exception as exc:
            log_event(
                event.payload.get("invoice_id"),
                None,
                "outbox_dispatch_failed",
                outbox_event_id=event.id,
                error=str(exc),
            )
            continue

        event.status = "DISPATCHED"
        event.dispatched_at = timezone.now()
        dispatched.append(event)

    if dispatched:
        with transaction.atomic():
            OutboxEvent.objects.bulk_update(dispatched, ["status", "dispatched_at"])
