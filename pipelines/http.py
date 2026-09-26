import time
from collections.abc import Callable

import httpx

from app.config import Settings


class RateLimiter:
    """Blocks so that calls to `wait()` happen at most `per_second` times a second."""

    def __init__(
        self,
        per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._next_at = 0.0

    def wait(self) -> None:
        now = self._clock()
        if now < self._next_at:
            self._sleep(self._next_at - now)
            now = self._next_at
        self._next_at = now + self.interval


def make_client(settings: Settings, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.user_agent},
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        transport=transport,
    )


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def send(
    client: httpx.Client,
    limiter: RateLimiter,
    request: httpx.Request,
    *,
    max_retries: int,
    backoff_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """Sends a rate-limited request, retrying 429/5xx with exponential backoff.

    A numeric Retry-After header takes precedence over the computed backoff.
    """
    for attempt in range(max_retries + 1):
        limiter.wait()
        response = client.send(request)
        if response.status_code not in RETRYABLE_STATUS or attempt == max_retries:
            response.raise_for_status()
            return response.content
        retry_after = response.headers.get("Retry-After", "")
        sleep(float(retry_after) if retry_after.isdigit() else backoff_seconds * 2**attempt)
    raise AssertionError("unreachable")
