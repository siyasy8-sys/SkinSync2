from datetime import date
from pathlib import Path

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
