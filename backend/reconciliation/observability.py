# structured logging and Prometheus metrics
import json
import logging
import uuid

from django.utils import timezone
from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger("reconciliation")


def new_trace_id(invoice_id) -> str:
    return f"{invoice_id}-{uuid.uuid4().hex[:8]}"


def log_event(invoice_id, trace_id, event: str, **kwargs) -> None:
    payload = {
        "event": event,
        "invoice_id": invoice_id,
        "trace_id": trace_id,
        "timestamp": timezone.now().isoformat(),
        **kwargs,
    }
    logger.info(json.dumps(payload))


invoices_ingested_total = Counter(
    "invoices_ingested_total", "Total invoices ingested", ["validation"]
)

invoices_processed_total = Counter(
    "invoices_processed_total",
    "Total invoices reconciled to a final outcome",
    ["status"],
)


invoices_dead_lettered_total = Counter(
    "invoices_dead_lettered_total", "Total invoices dead-lettered", ["reason"]
)


erp_requests_total = Counter(
    "erp_requests_total", "Total ERP API calls made", ["outcome"]
)


erp_request_latency_seconds = Histogram(
    "erp_request_latency_seconds",
    "ERP call duration in seconds",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)


erp_retries_total = Counter("erp_retries_total", "Total retry attempts", ["reason"])

cache_hits_total = Counter("cache_hits_total", "ERP cache hits", [])
cache_misses_total = Counter("cache_misses_total", "ERP cache misses", [])

celery_queue_depth = Gauge("celery_queue_depth", "Approximate pending Celery tasks")
