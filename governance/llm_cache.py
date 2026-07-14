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
from pathlib import Path

# A byte that cannot occur in the text fields, so ("ab", "c") and ("a", "bc")
# cannot hash to the same key.
_SEP = "\x00"


def cache_key(task: str, model: str, prompt_version: str, payload: str) -> str:
    """Stable sha256 over everything that could change the model's answer."""
    joined = _SEP.join((task, model, prompt_version, payload))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class Cache:
    """Flat sha256-keyed store of raw model responses."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> str | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def put(self, key: str, value: str) -> None:
        # Write to a temp file then replace, so an interrupted run cannot
        # leave a truncated response behind to be trusted on the next pass.
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(value, encoding="utf-8")
        tmp.replace(self._path(key))
