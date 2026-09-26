"""Local cache of raw source responses: data/raw/<source>/<snapshot-date>/<key>.

A run reads from the latest snapshot when one exists, so re-ingesting doesn't
call the source again. `refresh=True` starts a new snapshot dated today.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path


class RawCache:
    def __init__(
        self, root: Path, source: str, *, refresh: bool = False, today: date | None = None
    ) -> None:
        base = root / source
        latest = self._latest_snapshot(base)
        if refresh or latest is None:
            self.snapshot = (today or date.today()).isoformat()
        else:
            self.snapshot = latest
        self.dir = base / self.snapshot
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _latest_snapshot(base: Path) -> str | None:
        if not base.is_dir():
            return None
        snapshots = sorted(p.name for p in base.iterdir() if p.is_dir())
        return snapshots[-1] if snapshots else None

    def get(self, key: str) -> bytes | None:
        path = self.dir / key
        return path.read_bytes() if path.is_file() else None

    def put(self, key: str, data: bytes) -> None:
        path = self.dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic, so an interrupted run never leaves a partial file

    def fetch(
        self,
        key: str,
        fetch_fn: Callable[[], bytes],
        validate: Callable[[bytes], None] | None = None,
    ) -> bytes:
        """Returns cached bytes, or fetches and caches them.

        `validate` raises on a bad payload (e.g. an error body sent with HTTP 200).
        A bad fresh payload is never cached; a bad cached one is refetched.
        """
        cached = self.get(key)
        if cached is not None:
            try:
                if validate:
                    validate(cached)
                self.hits += 1
                return cached
            except ValueError:
                pass  # stale bad entry from before validation existed: refetch
        data = fetch_fn()
        if validate:
            validate(data)
        self.put(key, data)
        self.misses += 1
        return data
