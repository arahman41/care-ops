"""Content-addressed disk cache for LLM calls in the eval harness.

This is a reproducibility mechanism, not a performance one. A warm cache lets
`make eval-structuring` replay the exact model outputs that produced the
headline number, so the number can be regenerated without re-spending and
without drifting.

The key covers the model id and the prompt version as well as the payload.
That matters: if a judge prompt were edited or the judge model swapped and the
old verdicts were silently reused, the reported number would be a blend of two
different experiments. Changing either is a cache miss by construction.
"""
from __future__ import annotations

import hashlib
import threading
from collections import Counter
from pathlib import Path

# A byte that cannot occur in the text fields, so ("ab", "c") and ("a", "bc")
# cannot hash to the same key.
_SEP = "\x00"


def cache_key(task: str, model: str, prompt_version: str, payload: str) -> str:
    """Stable sha256 over everything that could change the model's answer."""
    joined = _SEP.join((task, model, prompt_version, payload))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class Cache:
    """Flat sha256-keyed store of raw model responses.

    Counts its own hits and misses (P3-2). The key covers the model, the prompt
    version and the payload, but NOT the window, so two windows of the same
    configuration collide by construction: a second window run against a warm
    cache replays the first window's outputs and reproduces its metrics
    exactly, which would put two identical points on a drift trend and call
    them a measurement. The counters are what let a caller detect that, so
    they are not diagnostics; they are part of the guard.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.hits: Counter = Counter()
        self.misses: Counter = Counter()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str, task: str = "unknown") -> str | None:
        path = self._path(key)
        # The harness fans out over eight workers, so an unsynchronised
        # counter would undercount by however many increments raced, and it
        # would undercount HITS, which is the direction that hides a replay.
        if not path.is_file():
            with self._lock:
                self.misses[task] += 1
            return None
        with self._lock:
            self.hits[task] += 1
        return path.read_text(encoding="utf-8")

    def stats(self) -> dict:
        """Hits and misses per task, plus totals. Safe to call mid-run."""
        with self._lock:
            hits, misses = dict(self.hits), dict(self.misses)
        return {
            "hits": hits,
            "misses": misses,
            "total_hits": sum(hits.values()),
            "total_misses": sum(misses.values()),
        }

    def put(self, key: str, value: str) -> None:
        # Write to a temp file then replace, so an interrupted run cannot
        # leave a truncated response behind to be trusted on the next pass.
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(value, encoding="utf-8")
        tmp.replace(self._path(key))
