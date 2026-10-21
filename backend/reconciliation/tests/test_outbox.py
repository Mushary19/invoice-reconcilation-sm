# outbox dispatch idempotency
from decimal import Decimal
from unittest.mock import patch

from reconciliation.models import Invoice, InvoiceStatus, OutboxEvent
from reconciliation.tasks import dispatch_outbox_events, reconcile_invoice


def _make_pending_invoice(**overrides):
    defaults = dict(vendor_id='V-3001', invoice_number='3001-11111',
                     po_number='PO-888888', amount=Decimal('500.00'),
                     currency='USD', status=InvoiceStatus.PENDING)
    defaults.update(overrides)
    return Invoice.objects.create(**defaults)


def test_outbox_double_dispatch_is_harmless(db):
    invoice = _make_pending_invoice()
    OutboxEvent.objects.create(invoice=invoice, event_type='reconcile_invoice',
                                payload={'invoice_id': invoice.id}, status='PENDING')

    mock_response = {'matched': True, 'po_number': invoice.po_number,
                      'po_amount': 500.00, 'currency': 'USD',
                      'status': 'OPEN', 'match_confidence': 'exact'}

    # get_cached_match/set_cached_match are patched out so this test doesn't
    # depend on real Redis being empty of any prior run's leftover cache
    # entry for this same vendor_id/po_number/amount/currency -- a stale hit
    # would skip the mocked call_erp entirely and falsely fail the
    # assert_called_once() below.
    with patch('reconciliation.tasks.call_erp', return_value=mock_response) as mock_erp, \
         patch('reconciliation.tasks.get_rate_limiter') as mock_limiter, \
         patch('reconciliation.tasks.get_cached_match', return_value=None), \
         patch('reconciliation.tasks.set_cached_match'):
        mock_limiter.return_value.acquire.return_value = True

        # Simulates the dispatcher firing .delay() for the same event twice
        # (e.g. it crashed after the first .delay() but before marking the
        # event DISPATCHED, then a later poll picked up the still-PENDING
        # event again). Sequential, not concurrent -- the race-condition
        # case is already covered by test_idempotency.py's Part B.
        reconcile_invoice.apply(args=[invoice.id])
        reconcile_invoice.apply(args=[invoice.id])

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.MATCHED
    # The second call should see status != PENDING/RETRYING and no-op via
    # reconcile_invoice's own claim guard -- proving the outbox's safety is
    # inherited from the task's idempotency, not a separate mechanism.
    mock_erp.assert_called_once()


def test_dispatch_outbox_events_marks_dispatched_and_batches(db):
    invoices = [_make_pending_invoice(invoice_number=f'3001-2222{i}', po_number=f'PO-77777{i}')
                for i in range(5)]
    events = [OutboxEvent.objects.create(invoice=inv, event_type='reconcile_invoice',
                                          payload={'invoice_id': inv.id}, status='PENDING')
              for inv in invoices]

    with patch('reconciliation.tasks.reconcile_invoice.delay') as mock_delay:
        dispatch_outbox_events()

    assert mock_delay.call_count == 5
    dispatched_ids = {c.args[0] for c in mock_delay.call_args_list}
    assert dispatched_ids == {inv.id for inv in invoices}

    for event in events:
        event.refresh_from_db()
        assert event.status == 'DISPATCHED'
        assert event.dispatched_at is not None


def test_dispatch_outbox_events_survives_partial_failure(db):
    invoices = [_make_pending_invoice(invoice_number=f'3001-3333{i}', po_number=f'PO-66666{i}')
                for i in range(3)]
    events = [OutboxEvent.objects.create(invoice=inv, event_type='reconcile_invoice',
                                          payload={'invoice_id': inv.id}, status='PENDING')
              for inv in invoices]

    # Simulates "Redis broker unreachable mid-batch": the second .delay()
    # call raises, the other two succeed.
    with patch('reconciliation.tasks.reconcile_invoice.delay',
               side_effect=[None, Exception('broker down'), None]):
        dispatch_outbox_events()  # must not raise -- per-event try/except must contain it

    for event in events:
        event.refresh_from_db()

    statuses = [event.status for event in events]
    # OutboxEvent has no defined ordering and dispatch_outbox_events()'s
    # query doesn't apply one, so which of the 3 rows the mock's 2nd
    # call_count corresponds to isn't guaranteed by Postgres row-return
    # order. What matters -- and is guaranteed -- is that exactly one event
    # (whichever the failing .delay() call landed on) is left PENDING for
    # the next poll to retry, and the other two are DISPATCHED.
    assert statuses.count('DISPATCHED') == 2
    assert statuses.count('PENDING') == 1
