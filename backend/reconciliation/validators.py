# malformed invoice rejection rules
from decimal import Decimal, InvalidOperation

REQUIRED_FIELDS = ["vendor_id", "invoice_number", "po_number", "amount", "currency"]
VALID_CURRENCIES = {"USD", "EUR", "GBP"}


def validate_invoice(data: dict) -> tuple[bool, str | None]:
    for field in REQUIRED_FIELDS:
        if field not in data:
            return False, "missing_required_field"

    vendor_id = data["vendor_id"]
    if not isinstance(vendor_id, str) or not vendor_id.strip():
        return False, "blank_vendor"

    try:
        amount = Decimal(str(data["amount"]))
    except (InvalidOperation, TypeError, ValueError):
        return False, "invalid_amount"
    if amount <= 0:
        return False, "negative_amount"

    if data["currency"] not in VALID_CURRENCIES:
        return False, "invalid_currency"

    po_number = data["po_number"]
    if not isinstance(po_number, str) or not po_number.strip():
        return False, "blank_po"

    return True, None
