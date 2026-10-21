# malformed invoice rejection rules
from reconciliation.validators import validate_invoice


def test_valid_invoice_passes():
    data = {'vendor_id': 'V-1001', 'invoice_number': '1001-12345',
            'po_number': 'PO-000001', 'amount': 1000.00, 'currency': 'USD'}
    valid, reason = validate_invoice(data)
    assert valid is True
    assert reason is None


def test_missing_amount_rejected():
    # mirrors _corrupt()'s no_amount mode
    data = {'vendor_id': 'V-1001', 'invoice_number': '1001-12345',
            'po_number': 'PO-000001', 'currency': 'USD'}  # amount key absent
    valid, reason = validate_invoice(data)
    assert valid is False
    assert reason == 'missing_required_field'


def test_blank_vendor_rejected():
    # mirrors _corrupt()'s blank_vendor mode
    data = {'vendor_id': '', 'invoice_number': '1001-12345',
            'po_number': 'PO-000001', 'amount': 1000.00, 'currency': 'USD'}
    valid, reason = validate_invoice(data)
    assert valid is False
    assert reason == 'blank_vendor'


def test_negative_amount_rejected():
    # mirrors _corrupt()'s neg_amount mode
    data = {'vendor_id': 'V-1001', 'invoice_number': '1001-12345',
            'po_number': 'PO-000001', 'amount': -500.00, 'currency': 'USD'}
    valid, reason = validate_invoice(data)
    assert valid is False
    assert reason == 'negative_amount'


def test_bad_currency_rejected():
    # mirrors _corrupt()'s bad_currency mode
    data = {'vendor_id': 'V-1001', 'invoice_number': '1001-12345',
            'po_number': 'PO-000001', 'amount': 1000.00, 'currency': 'XYZ'}
    valid, reason = validate_invoice(data)
    assert valid is False
    assert reason == 'invalid_currency'


def test_blank_po_rejected():
    # mirrors _corrupt()'s no_po mode
    data = {'vendor_id': 'V-1001', 'invoice_number': '1001-12345',
            'po_number': '', 'amount': 1000.00, 'currency': 'USD'}
    valid, reason = validate_invoice(data)
    assert valid is False
    assert reason == 'blank_po'


def test_non_numeric_amount_rejected():
    # defensive case not explicitly in _corrupt() but worth covering since
    # real upstream feeds could send garbage
    data = {'vendor_id': 'V-1001', 'invoice_number': '1001-12345',
            'po_number': 'PO-000001', 'amount': 'not_a_number', 'currency': 'USD'}
    valid, reason = validate_invoice(data)
    assert valid is False
    assert reason == 'invalid_amount'
