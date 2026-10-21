# ERP HTTP client with timeout + session pooling
from decimal import Decimal

import requests
from django.conf import settings


def _build_session() -> requests.Session:
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=10,
        pool_maxsize=10,
        max_retries=0,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_session = _build_session()


def call_erp(vendor_id: str, po_number: str, amount: Decimal, currency: str) -> dict:

    base_url = getattr(settings, "ERP_BASE_URL", "http://mock-erp:9100")
    response = _session.get(
        f"{base_url}/erp/purchase-orders/match",
        params={
            "po_number": po_number,
            "vendor_id": vendor_id,
            "amount": str(amount),
            "currency": currency,
        },
        timeout=5,
    )
    response.raise_for_status()
    return response.json()
