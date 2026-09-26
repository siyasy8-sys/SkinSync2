from collections.abc import Callable

import httpx
import pytest

from pipelines.http import RateLimiter, send


def test_rate_limiter_spaces_calls() -> None:
    now = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(2.0, clock=lambda: now[0], sleep=sleep)
    limiter.wait()  # first call never waits
    limiter.wait()
    now[0] += 5.0  # idle long enough that the next call needn't wait
    limiter.wait()

    assert sleeps == [0.5]


def _limiter() -> RateLimiter:
    return RateLimiter(1000.0, clock=lambda: 0.0, sleep=lambda _: None)


def test_send_retries_429_honoring_retry_after_then_succeeds() -> None:
    statuses = iter([429, 503, 200])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        headers = {"Retry-After": "7"} if status == 429 else {}
        return httpx.Response(status, headers=headers, content=b"ok")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = send(
            client,
            _limiter(),
            client.build_request("GET", "https://example.test/"),
            max_retries=3,
            backoff_seconds=0.5,
            sleep=sleeps.append,
        )

    assert body == b"ok"
    assert sleeps == [7.0, 1.0]  # Retry-After, then 0.5 * 2**1


def _send_expecting_error(handler: Callable[[httpx.Request], httpx.Response]) -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        send(
            client,
            _limiter(),
            client.build_request("GET", "https://example.test/"),
            max_retries=2,
            backoff_seconds=0,
            sleep=lambda _: None,
        )


def test_send_gives_up_after_max_retries_and_does_not_retry_404() -> None:
    calls: dict[int, int] = {429: 0, 404: 0}

    def responder(status: int) -> Callable[[httpx.Request], httpx.Response]:
        def handler(request: httpx.Request) -> httpx.Response:
            calls[status] += 1
            return httpx.Response(status)

        return handler

    _send_expecting_error(responder(429))
    _send_expecting_error(responder(404))

    assert calls == {429: 3, 404: 1}  # 1 try + 2 retries; 404 is not retried
