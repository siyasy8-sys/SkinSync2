from datetime import date
from pathlib import Path

import pytest

from pipelines.cache import RawCache


def test_miss_fetches_and_stores_then_hit_skips_fetch(tmp_path: Path) -> None:
    calls = []
    cache = RawCache(tmp_path, "src", today=date(2026, 9, 26))

    def fetch() -> bytes:
        calls.append(1)
        return b"payload"

    assert cache.fetch("a.json", fetch) == b"payload"
    assert cache.fetch("a.json", fetch) == b"payload"
    assert len(calls) == 1
    assert (cache.hits, cache.misses) == (1, 1)
    assert (tmp_path / "src" / "2026-09-26" / "a.json").read_bytes() == b"payload"


def test_reuses_latest_snapshot_unless_refresh(tmp_path: Path) -> None:
    RawCache(tmp_path, "src", today=date(2026, 9, 1)).put("a.json", b"old")
    RawCache(tmp_path, "src", refresh=True, today=date(2026, 9, 2)).put("a.json", b"newer")

    later = RawCache(tmp_path, "src", today=date(2026, 9, 30))
    assert later.snapshot == "2026-09-02"
    assert later.get("a.json") == b"newer"

    refreshed = RawCache(tmp_path, "src", refresh=True, today=date(2026, 9, 30))
    assert refreshed.snapshot == "2026-09-30"
    assert refreshed.get("a.json") is None


def _reject_bad(data: bytes) -> None:
    if data == b"bad":
        raise ValueError("bad payload")


def test_invalid_fresh_payload_raises_and_is_not_cached(tmp_path: Path) -> None:
    cache = RawCache(tmp_path, "src")

    with pytest.raises(ValueError, match="bad payload"):
        cache.fetch("a.json", lambda: b"bad", validate=_reject_bad)

    assert cache.get("a.json") is None


def test_invalid_cached_payload_is_refetched(tmp_path: Path) -> None:
    cache = RawCache(tmp_path, "src")
    cache.put("a.json", b"bad")  # e.g. cached before validation existed

    assert cache.fetch("a.json", lambda: b"good", validate=_reject_bad) == b"good"
    assert cache.get("a.json") == b"good"
    assert (cache.hits, cache.misses) == (0, 1)
