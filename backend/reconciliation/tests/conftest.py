# pytest fixtures: DB, Redis, mock ERP responses
import pytest
import redis
from django.conf import settings


@pytest.fixture
def redis_client():
    """
    Real Redis client for tests, flushed before each test to avoid
    cross-test pollution. Uses db=15 (separate from settings.REDIS_URL's
    normal db) so tests never collide with data a running docker-compose
    stack might have.

    If Redis isn't reachable, this fails loudly at the flushdb() call
    (redis.exceptions.ConnectionError) rather than silently treating every
    subsequent get_cached_match() call as a cache miss -- a passing test
    suite must mean the cache logic actually works, not that Redis happened
    to be down.
    """
    client = redis.Redis.from_url(settings.REDIS_URL.rsplit('/', 1)[0] + '/15')
    try:
        client.flushdb()
    except redis.exceptions.RedisError as exc:
        pytest.fail(f"Redis is not reachable at {settings.REDIS_URL} (is Redis running?): {exc}")
    yield client
    client.flushdb()


@pytest.fixture
def clean_db(db):
    """Marker fixture making test intent explicit; relies on pytest-django's
    `db` fixture for per-test transaction rollback."""
    yield
