# global 10 req/sec enforcement across workers
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from reconciliation.rate_limiter import RateLimiter


def test_rate_limiter_holds_exactly_n_under_concurrent_load(redis_client):
    limiter = RateLimiter(redis_client, key_prefix='test_concurrent', limit=10)
    n = 50
    barrier = threading.Barrier(n)

    # If the 50 calls straddle a wall-clock second boundary, some land in
    # bucket N and others in bucket N+1, splitting the load across two
    # buckets and invalidating the exactly-10 assertion for reasons
    # unrelated to the Lua script's atomicity. Wait for a fresh second
    # boundary whenever less than 200ms remains, so all 50 threads land in
    # the same bucket with a comfortable margin.
    remaining = 1 - (time.time() % 1)
    if remaining < 0.2:
        time.sleep(remaining + 0.05)

    def worker():
        barrier.wait()
        return limiter.acquire()

    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(worker) for _ in range(n)]
        results = [f.result() for f in futures]

    true_count = sum(1 for r in results if r is True)
    false_count = sum(1 for r in results if r is False)

    assert true_count == 10
    assert false_count == 40
