# bulk NDJSON loader, transactional outbox write
import json
from collections import Counter
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from reconciliation.models import Invoice, InvoiceStatus, OutboxEvent
from reconciliation.observability import invoices_ingested_total
from reconciliation.validators import validate_invoice


class Command(BaseCommand):
    help = (
        "Bulk-load invoices from an NDJSON file, validating and writing "
        "Invoice + OutboxEvent atomically per record."
    )

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str)
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Log progress every N records processed (does not batch the "
            "DB writes themselves -- each record is its own transaction, "
            "see note below on why)",
        )

    def handle(self, *args, **options):
        file_path = options["file_path"]
        batch_size = options["batch_size"]

        processed_count = 0
        inserted_count = 0
        duplicate_count = 0
        rejected_count = 0
        rejection_reasons = Counter()

        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                processed_count += 1

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    rejected_count += 1
                    rejection_reasons["invalid_json"] += 1
                    invoices_ingested_total.labels(validation="rejected").inc()
                    if processed_count % batch_size == 0:
                        self.stdout.write(f"Processed {processed_count}...")
                    continue

                is_valid, reason = validate_invoice(data)
                if not is_valid:
                    rejected_count += 1
                    rejection_reasons[reason] += 1
                    invoices_ingested_total.labels(validation="rejected").inc()
                    if processed_count % batch_size == 0:
                        self.stdout.write(f"Processed {processed_count}...")
                    continue

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
                                parse_datetime(data["issued_at"])
                                if data.get("issued_at")
                                else None
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
                    invoices_ingested_total.labels(validation="valid").inc()
                    inserted_count += 1
                except IntegrityError:

                    duplicate_count += 1

                if processed_count % batch_size == 0:
                    self.stdout.write(f"Processed {processed_count}...")

        reasons_summary = ", ".join(
            f"{reason}={count}" for reason, count in rejection_reasons.items()
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. processed={processed_count} inserted={inserted_count} "
                f"duplicates={duplicate_count} rejected={rejected_count} "
                f"({reasons_summary})"
            )
        )
