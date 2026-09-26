from pipelines.http import RateLimiter


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
