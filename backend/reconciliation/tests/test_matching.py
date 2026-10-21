# ERP matching rule verification
#
# reconcile_invoice.apply(args=[...]) runs the task function synchronously,
# in-process -- unlike .delay(), which requires a real broker connection and
# a running worker. That's what makes these fast, docker-compose-free tests
# possible.
#
# Every test below except the cache-hit one also patches get_cached_match to
# force a cache miss. Without that, the first test's real set_cached_match()
# write (all fixture invoices share the same vendor_id/po_number/amount/
# currency) would be read back as a cache hit by every later test, silently
# skipping their mocked call_erp() and asserting on stale data from the
# first test instead of their own. Patching it out keeps this file free of
# both the cross-test pollution and any real Redis dependency.
from decimal import Decimal
from unittest.mock import patch

from reconciliation.models import Invoice, InvoiceStatus
from reconciliation.tasks import reconcile_invoice


def _make_pending_invoice(**overrides):
    defaults = dict(vendor_id='V-1003', invoice_number='1003-11111',
                     po_number='PO-000001', amount=Decimal('4210.55'),
                     currency='USD', status=InvoiceStatus.PENDING)
    defaults.update(overrides)
    return Invoice.objects.create(**defaults)


def test_matched_response_sets_matched_status(db):
    invoice = _make_pending_invoice()
    with patch('reconciliation.tasks.call_erp') as mock_erp, \
         patch('reconciliation.tasks.get_rate_limiter') as mock_limiter, \
         patch('reconciliation.tasks.get_cached_match', return_value=None), \
         patch('reconciliation.tasks.set_cached_match'):
        mock_limiter.return_value.acquire.return_value = True
        mock_erp.return_value = {'matched': True, 'po_number': 'PO-000001',
                                  'po_amount': 4210.55, 'currency': 'USD',
                                  'status': 'OPEN', 'match_confidence': 'exact'}
        reconcile_invoice.apply(args=[invoice.id])
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.MATCHED
    assert invoice.erp_response['matched'] is True


def test_po_not_found_sets_unmatched(db):
    invoice = _make_pending_invoice()
    with patch('reconciliation.tasks.call_erp') as mock_erp, \
         patch('reconciliation.tasks.get_rate_limiter') as mock_limiter, \
         patch('reconciliation.tasks.get_cached_match', return_value=None), \
         patch('reconciliation.tasks.set_cached_match'):
        mock_limiter.return_value.acquire.return_value = True
        mock_erp.return_value = {'matched': False, 'reason': 'po_not_found',
                                  'po_number': 'PO-000001'}
        reconcile_invoice.apply(args=[invoice.id])
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.UNMATCHED
    assert invoice.erp_response['reason'] == 'po_not_found'


def test_vendor_mismatch_sets_unmatched(db):
    invoice = _make_pending_invoice()
    with patch('reconciliation.tasks.call_erp') as mock_erp, \
         patch('reconciliation.tasks.get_rate_limiter') as mock_limiter, \
         patch('reconciliation.tasks.get_cached_match', return_value=None), \
         patch('reconciliation.tasks.set_cached_match'):
        mock_limiter.return_value.acquire.return_value = True
        mock_erp.return_value = {'matched': False, 'reason': 'vendor_mismatch',
                                  'po_number': 'PO-000001', 'expected_vendor': 'V-1099'}
        reconcile_invoice.apply(args=[invoice.id])
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.UNMATCHED
    assert invoice.erp_response['reason'] == 'vendor_mismatch'


def test_amount_out_of_tolerance_sets_unmatched(db):
    invoice = _make_pending_invoice()
    with patch('reconciliation.tasks.call_erp') as mock_erp, \
         patch('reconciliation.tasks.get_rate_limiter') as mock_limiter, \
         patch('reconciliation.tasks.get_cached_match', return_value=None), \
         patch('reconciliation.tasks.set_cached_match'):
        mock_limiter.return_value.acquire.return_value = True
        mock_erp.return_value = {'matched': False, 'reason': 'amount_out_of_tolerance',
                                  'po_number': 'PO-000001', 'po_amount': 5000.00}
        reconcile_invoice.apply(args=[invoice.id])
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.UNMATCHED
    assert invoice.erp_response['reason'] == 'amount_out_of_tolerance'


def test_cache_hit_skips_erp_call_entirely(db):
    invoice = _make_pending_invoice()
    with patch('reconciliation.tasks.get_cached_match') as mock_cache, \
         patch('reconciliation.tasks.call_erp') as mock_erp:
        mock_cache.return_value = {'matched': True, 'po_amount': 4210.55}
        reconcile_invoice.apply(args=[invoice.id])
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.MATCHED
    mock_erp.assert_not_called()  # proves cache actually bypasses the ERP call,
                                    # not just that both happen to produce the right answer
