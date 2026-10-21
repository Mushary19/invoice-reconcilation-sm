# ingest, summary, healthz, readyz, metrics endpoints
from datetime import timedelta
from decimal import Decimal

import redis
from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Invoice, InvoiceStatus, OutboxEvent
from .observability import invoices_ingested_total
from .validators import validate_invoice


@api_view(["POST"])
def ingest_invoice(request):

    data = request.data

    valid, reason = validate_invoice(data)
    if not valid:
        return Response({"error": "validation_failed", "reason": reason}, status=400)

    try:
        with transaction.atomic():
            invoice = Invoice.objects.create(
                vendor_id=data["vendor_id"],
                invoice_number=data["invoice_number"],
                po_number=data["po_number"],
                amount=Decimal(str(data["amount"])),
                currency=data["currency"],
                invoice_id=data.get("invoice_id", ""),
                content_hash=data.get("content_hash", ""),
                vendor_name=data.get("vendor_name"),
                cost_center=data.get("cost_center"),
                source=data.get("source"),
                issued_at=(
                    parse_datetime(data["issued_at"]) if data.get("issued_at") else None
                ),
                submitted_at=(
                    parse_datetime(data["submitted_at"])
                    if data.get("submitted_at")
                    else None
                ),
                status=InvoiceStatus.PENDING,
            )
            OutboxEvent.objects.create(
                invoice=invoice,
                event_type="reconcile_invoice",
                payload={"invoice_id": invoice.id},
                status="PENDING",
            )
    except IntegrityError:
        return Response(
            {
                "error": "duplicate",
                "detail": "invoice with this identity already exists",
            },
            status=409,
        )

    invoices_ingested_total.labels(validation="valid").inc()
    return Response({"id": invoice.id, "status": invoice.status}, status=202)


@api_view(["GET"])
def summary(request):
    now = timezone.now()
    one_hour_ago = now - timedelta(hours=1)
    five_min_ago = now - timedelta(minutes=5)

    totals = {
        status.value: Invoice.objects.filter(status=status.value).count()
        for status in InvoiceStatus
    }
    totals["total"] = sum(totals.values())

    def match_rate(queryset):
        matched = queryset.filter(status=InvoiceStatus.MATCHED).count()
        unmatched = queryset.filter(status=InvoiceStatus.UNMATCHED).count()
        denominator = matched + unmatched
        if denominator == 0:
            return 0
        return round(matched / denominator * 100, 2)

    match_rates = {
        "all_time_percent": match_rate(Invoice.objects),
        "last_hour_percent": match_rate(
            Invoice.objects.filter(updated_at__gte=one_hour_ago)
        ),
    }

    stuck = {
        "retrying_over_5min": Invoice.objects.filter(
            status=InvoiceStatus.RETRYING, updated_at__lt=five_min_ago
        ).count(),
        "processing_over_5min": Invoice.objects.filter(
            status=InvoiceStatus.PROCESSING, updated_at__lt=five_min_ago
        ).count(),
    }

    return Response(
        {
            "totals": totals,
            "match_rates": match_rates,
            "stuck": stuck,
            "timestamp": now.isoformat(),
        },
        status=200,
    )


def _check_dependencies():
    checks = {"database": False, "redis": False}

    try:
        Invoice.objects.exists()
        checks["database"] = True
    except Exception:
        pass

    try:
        redis.Redis.from_url(settings.REDIS_URL).ping()
        checks["redis"] = True
    except Exception:
        pass

    return checks


@api_view(["GET"])
def healthz(request):

    checks = _check_dependencies()
    if all(checks.values()):
        return Response(
            {"status": "ok", "timestamp": timezone.now().isoformat()}, status=200
        )
    return Response({"status": "error", "checks": checks}, status=503)


@api_view(["GET"])
def readyz(request):

    checks = _check_dependencies()
    if all(checks.values()):
        return Response(
            {"status": "ok", "timestamp": timezone.now().isoformat()}, status=200
        )
    return Response({"status": "error", "checks": checks}, status=503)


@api_view(["GET"])
def metrics(request):
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
