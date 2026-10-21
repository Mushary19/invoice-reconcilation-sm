# Redis TTL cache for ERP match results
import json
import logging
from decimal import Decimal

import redis.exceptions

logger = logging.getLogger(__name__)


def build_cache_key(
    vendor_id: str, po_number: str, amount: Decimal, currency: str
) -> str:
    normalized_amount = amount.quantize(Decimal("0.01"))
    return f"erp:match:{vendor_id}:{po_number}:{normalized_amount}:{currency}"


def get_cached_match(
    redis_client, vendor_id, po_number, amount, currency
) -> dict | None:
    key = build_cache_key(vendor_id, po_number, amount, currency)
    try:
        cached = redis_client.get(key)
    except redis.exceptions.RedisError as exc:
        logger.warning("Cache read failed for key %s: %s", key, exc)
        return None
    if cached is None:
        return None
    return json.loads(cached)


def set_cached_match(
    redis_client,
    vendor_id,
    po_number,
    amount,
    currency,
    response: dict,
    ttl_seconds: int = 300,
) -> None:
    key = build_cache_key(vendor_id, po_number, amount, currency)
    try:
        redis_client.set(key, json.dumps(response), ex=ttl_seconds)
    except redis.exceptions.RedisError as exc:
        logger.warning("Cache write failed for key %s: %s", key, exc)
