# transient vs permanent error classification
from requests.exceptions import ConnectionError, HTTPError, Timeout

from reconciliation.retry_policy import PermanentError, TransientError, backoff_seconds, classify_error


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def test_timeout_is_transient():
    assert classify_error(Timeout()) == TransientError


def test_connection_error_is_transient():
    assert classify_error(ConnectionError()) == TransientError


def test_429_is_transient():
    exc = HTTPError(response=FakeResponse(429))
    assert classify_error(exc) == TransientError


def test_503_is_transient():
    exc = HTTPError(response=FakeResponse(503))
    assert classify_error(exc) == TransientError


def test_500_is_transient():
    # "other 5xx -> transient" per spec
    exc = HTTPError(response=FakeResponse(500))
    assert classify_error(exc) == TransientError


def test_400_is_permanent():
    exc = HTTPError(response=FakeResponse(400))
    assert classify_error(exc) == PermanentError


def test_404_is_permanent():
    exc = HTTPError(response=FakeResponse(404))
    assert classify_error(exc) == PermanentError


def test_unrecognized_exception_fails_closed_to_permanent():
    assert classify_error(ValueError("unexpected")) == PermanentError


def test_backoff_increases_with_attempt():
    # not exact values (jitter), but confirm the exponential trend holds
    assert 1 <= backoff_seconds(0) <= 6
    assert 2 <= backoff_seconds(1) <= 7
    assert 4 <= backoff_seconds(2) <= 9
    assert 8 <= backoff_seconds(3) <= 13
    assert 16 <= backoff_seconds(4) <= 21


def test_backoff_caps_at_300_plus_jitter():
    # a large attempt number shouldn't blow past the cap
    result = backoff_seconds(20)  # 2**20 is huge, must be capped
    assert 300 <= result <= 305
