# error classification + exponential backoff
import random

import requests

MAX_RETRIES = 5


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


def classify_error(exc: Exception) -> type:

    if isinstance(
        exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)
    ):
        return TransientError

    if isinstance(exc, requests.exceptions.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code == 429:
            return TransientError
        if status_code is not None and 500 <= status_code < 600:
            return TransientError
        return PermanentError

    return PermanentError


def backoff_seconds(attempt: int) -> int:
    return min(2**attempt, 300) + random.randint(0, 5)
