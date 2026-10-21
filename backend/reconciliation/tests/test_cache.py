# cache hit/miss/expiry behavior
import time
from decimal import Decimal

from reconciliation.cache import get_cached_match, set_cached_match


def test_cache_miss_returns_none(redis_client):
    result = get_cached_match(redis_client, 'V-1001', 'PO-000001', Decimal('1000.00'), 'USD')
    assert result is None


def test_cache_set_then_hit(redis_client):
    response = {'matched': True, 'po_amount': 1000.00}
    set_cached_match(redis_client, 'V-1001', 'PO-000001', Decimal('1000.00'), 'USD', response)
    result = get_cached_match(redis_client, 'V-1001', 'PO-000001', Decimal('1000.00'), 'USD')
    assert result == response


def test_cache_key_normalizes_decimal_representation(redis_client):
    # same numeric value, different Decimal construction -> same cache entry
    response = {'matched': True}
    set_cached_match(redis_client, 'V-1001', 'PO-000001', Decimal('1000.0'), 'USD', response)
    result = get_cached_match(redis_client, 'V-1001', 'PO-000001', Decimal('1000.00'), 'USD')
    assert result == response  # hits despite different Decimal construction


def test_cache_expires_after_ttl(redis_client):
    response = {'matched': True}
    set_cached_match(redis_client, 'V-1001', 'PO-000001', Decimal('1000.00'), 'USD', response, ttl_seconds=1)
    time.sleep(1.5)
    result = get_cached_match(redis_client, 'V-1001', 'PO-000001', Decimal('1000.00'), 'USD')
    assert result is None  # expired


def test_different_lookups_dont_collide(redis_client):
    # different po_number should be a different cache entry entirely
    set_cached_match(redis_client, 'V-1001', 'PO-000001', Decimal('1000.00'), 'USD', {'matched': True})
    result = get_cached_match(redis_client, 'V-1001', 'PO-000002', Decimal('1000.00'), 'USD')
    assert result is None
