# concurrent duplicate submission handling
#
# @pytest.mark.django_db(transaction=True) is used instead of the plain `db`
# fixture in both tests below. The plain `db` fixture wraps a test in a
# single outer transaction that's rolled back at the end -- fine for
# single-threaded tests, but select_for_update()'s locking only becomes
# observable across REAL, separately-committed transactions on SEPARATE
# connections. transaction=True (TransactionTestCase-style) commits for
# real, so each worker thread's own Django DB connection (thread-local by
# default) can actually see and block on the other threads' locks/commits --
# which is the exact mechanism being verified here. Each worker also calls
# connection.close() when done, since Django doesn't auto-close connections
# opened by threads outside the normal request/response cycle.
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import connection

from reconciliation.models import Invoice, InvoiceStatus
from reconciliation.tasks import _claim_invoice, reconcile_invoice


def _make_pending_invoice(**overrides):
    defaults = dict(vendor_id='V-2001', invoice_number='2001-99999',
                     po_number='PO-999999', amount=Decimal('1234.56'),
                     currency='USD', status=InvoiceStatus.PENDING)
    defaults.update(overrides)
    return Invoice.objects.create(**defaults)


@pytest.mark.django_db(transaction=True)
def test_claim_invoice_is_race_safe_under_concurrency():
    invoice = _make_pending_invoice()
    n = 20
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()  # force all N threads to hit select_for_update() at once
        try:
            return _claim_invoice(invoice.id)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(worker) for _ in range(n)]
        results = [f.result() for f in futures]

    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]

    assert len(winners) == 1
    assert len(losers) == n - 1

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PROCESSING


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_task_execution_produces_one_outcome():
    invoice = _make_pending_invoice()
    n = 10
    barrier = threading.Barrier(n)

    mock_response = {'matched': True, 'po_number': invoice.po_number,
                      'po_amount': 1234.56, 'currency': 'USD',
                      'status': 'OPEN', 'match_confidence': 'exact'}

    def worker():
        barrier.wait()
        try:
            reconcile_invoice.apply(args=[invoice.id])
        finally:
            connection.close()

    # Patched once, outside the thread pool -- unittest.mock.patch's
    # enter/exit is not safe to call concurrently from multiple threads
    # against the same target. All threads share this single mock.
    #
    # get_cached_match/set_cached_match are also patched out here: without
    # this, the winner thread's real cache write (all fixture invoices use
    # the same vendor_id/po_number/amount/currency) can survive in Redis
    # past this test and cause a false failure in a later run or in the
    # combined suite, where a stale hit skips the mocked call_erp entirely.
    with patch('reconciliation.tasks.call_erp', return_value=mock_response) as mock_erp, \
         patch('reconciliation.tasks.get_rate_limiter') as mock_limiter, \
         patch('reconciliation.tasks.get_cached_match', return_value=None), \
         patch('reconciliation.tasks.set_cached_match'):
        mock_limiter.return_value.acquire.return_value = True
        with ThreadPoolExecutor(max_workers=n) as executor:
            futures = [executor.submit(worker) for _ in range(n)]
            for f in futures:
                f.result()

    assert Invoice.objects.filter(id=invoice.id).count() == 1
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.MATCHED
    mock_erp.assert_called_once()
