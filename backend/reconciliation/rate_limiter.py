# Redis Lua-based global rate limiter
import time

import redis
from django.conf import settings

RATE_LIMIT_SCRIPT = """
local bucket_key = KEYS[1] .. ':' .. math.floor(tonumber(ARGV[2]))
local limit = tonumber(ARGV[1])
local count = redis.call('INCR', bucket_key)
if count == 1 then
    redis.call('EXPIRE', bucket_key, 2)
end
if count <= limit then
    return 1
else
    return 0
end
"""


class RateLimiter:
    def __init__(self, redis_client, key_prefix='erp_rate_limit', limit=10):
        self.redis_client = redis_client
        self.key_prefix = key_prefix
        self.limit = limit
        self._script = redis_client.register_script(RATE_LIMIT_SCRIPT)

    def acquire(self) -> bool:
        result = self._script(keys=[self.key_prefix], args=[self.limit, time.time()])
        return result == 1


def get_rate_limiter() -> RateLimiter:
    client = redis.Redis.from_url(settings.REDIS_URL)
    limit = getattr(settings, 'ERP_RATE_LIMIT', 10)
    return RateLimiter(client, key_prefix='erp_rate_limit', limit=limit)
