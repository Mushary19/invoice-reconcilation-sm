from django.db import models


class InvoiceStatus(models.TextChoices):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    MATCHED = "MATCHED"
    UNMATCHED = "UNMATCHED"
    RETRYING = "RETRYING"
    DEAD_LETTERED = "DEAD_LETTERED"


class Invoice(models.Model):
    vendor_id = models.CharField(max_length=10)
    invoice_number = models.CharField(max_length=20)
    po_number = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)

    invoice_id = models.CharField(max_length=30)

    content_hash = models.CharField(max_length=16, db_index=True)

    status = models.CharField(
        max_length=20,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.PENDING,
        db_index=True,
    )
    retry_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)
    erp_response = models.JSONField(null=True, blank=True)

    vendor_name = models.CharField(max_length=255, null=True, blank=True)
    cost_center = models.CharField(max_length=50, null=True, blank=True)
    source = models.CharField(max_length=50, null=True, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        unique_together = (
            ("vendor_id", "invoice_number", "po_number", "amount", "currency"),
        )
        indexes = [
            models.Index(fields=["status", "updated_at"]),
        ]

    def __str__(self):
        return f"{self.invoice_number} ({self.status})"


class OutboxEvent(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "PENDING"),
        ("DISPATCHED", "DISPATCHED"),
    ]

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="outbox_events"
    )
    event_type = models.CharField(max_length=50)
    payload = models.JSONField()
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="PENDING", db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"]),
        ]
