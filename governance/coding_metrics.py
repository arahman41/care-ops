"""Deduplicate a note's suggested codes, compute denominators, attribute the
floor. Pure: no API, no DB. Consumes CodingOutput, uses only shared.vocab.

Every counting rule here is pinned by the P2-4 spec §1 and §5. The two that
matter most: dedup is on vocab.normalize(code) ALONE and PER NOTE, and a
not_found/unchecked conflict resolves to not_found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from shared import vocab
from shared.schemas import CodingOutput
from shared.vocab import VocabularyStatus

# Benchmark-side heuristics, documented as such (spec §5c). shared.vocab has
# normalize and _CPT_RE and no general shape test, so these live here.
_CODE_SHAPE_RE = re.compile(r"^[A-Z0-9]{3,7}$")
# normalize uppercases and strips the DOT ONLY, so these survive as bare
# alphanumerics, pass a shape test, and would otherwise inflate cause 1.
_PLACEHOLDERS = frozenset({"NONE", "UNKNOWN", "TBD", "NA", "NIL", "PENDING"})

# not_found outranks unchecked; verified cannot co-occur with either.
_STATUS_RANK = {"not_found": 2, "unchecked": 1}


@dataclass(frozen=True)
class DedupedCode:
    key: str                          # vocab.normalize(code)
    status: VocabularyStatus
    systems_seen: tuple[str, ...]     # every declared system, retained
    descriptions: tuple[str, ...]     # model prose, for the roster only


@dataclass(frozen=True)
class Denominators:
    verified: int
    not_found: int
    unchecked: int

    @property
    def checkable(self) -> int:
        return self.verified + self.not_found

    @property
    def total(self) -> int:
        return self.verified + self.not_found + self.unchecked


@dataclass(frozen=True)
class FloorBand:
    cause1: int          # fabricated (residual)
    cause2: int          # real but absent from pin (0 unless a floor pin resolves it)
    cause3: int          # CPT-shaped, unverifiable (UPPER bound)
    cause4: int          # degenerate input
    checkable: int
    not_found: int

    @property
    def lower(self) -> float:
        """Lower bound on the floor, percentage points: the decidable artifact
        causes as a share of checkable codes. cause2 is included only when a
        floor pin resolved it (default 0)."""
        if not self.checkable:
            return 0.0
        return 100.0 * (self.cause4 + self.cause3 + self.cause2) / self.checkable

    @property
    def upper(self) -> float:
        """Upper bound on the floor, percentage points: the full not-found rate.
        Every not_found code is one of the four causes."""
        if not self.checkable:
            return 0.0
        return 100.0 * self.not_found / self.checkable


def dedupe_note(out: CodingOutput) -> list[DedupedCode]:
    """Collapse a note's codes on normalize(code) alone. Order is stable by
    first appearance so the roster and any diff are deterministic."""
    order: list[str] = []
    groups: dict[str, dict] = {}
    for c in out.codes:
        key = vocab.normalize(c.code)
        if key not in groups:
            groups[key] = {"statuses": [], "systems": [], "descriptions": []}
            order.append(key)
        # Recompute status from (system, code); never trust the stored one when
        # dedupe could merge differently-labelled occurrences.
        groups[key]["statuses"].append(vocab.classify(c.system, c.code))
        groups[key]["systems"].append(c.system)
        groups[key]["descriptions"].append(c.description)

    deduped: list[DedupedCode] = []
    for key in order:
        g = groups[key]
        deduped.append(DedupedCode(
            key=key,
            status=_resolve(g["statuses"]),
            systems_seen=tuple(g["systems"]),
            descriptions=tuple(g["descriptions"]),
        ))
    return deduped


def _resolve(statuses: list[VocabularyStatus]) -> VocabularyStatus:
    if "verified" in statuses:
        return "verified"          # cannot conflict; wins trivially
    return max(statuses, key=lambda s: _STATUS_RANK[s])   # not_found > unchecked


def note_denominators(deduped: list[DedupedCode]) -> Denominators:
    v = sum(c.status == "verified" for c in deduped)
    nf = sum(c.status == "not_found" for c in deduped)
    un = sum(c.status == "unchecked" for c in deduped)
    return Denominators(verified=v, not_found=nf, unchecked=un)


def _cause_of(code: DedupedCode) -> int:
    """Attribute one not_found code to a floor cause. Order 4 -> 3 -> 1.
    cause 2 needs a prior-release pin and is handled by the caller when a floor
    pin is supplied; here it never fires, so cause-2 codes fall to cause 1."""
    key = code.key
    if key in _PLACEHOLDERS or not _CODE_SHAPE_RE.match(key):
        return 4
    if vocab._looks_like_cpt(key) and any(s != "CPT" for s in code.systems_seen):
        return 3
    return 1


def floor_band(deduped: list[DedupedCode], floor_members=None) -> FloorBand:
    """Attribute this arm's not_found codes to floor causes.

    floor_members: optional frozenset of normalized keys known to be real in a
    prior release but absent from the current pin (spec §5a). When supplied, a
    not_found code in it is cause 2 instead of cause 1. Default None keeps cause
    2 empty and folded into the residual, which is the approved default.
    """
    denom = note_denominators(deduped)
    c1 = c2 = c3 = c4 = 0
    for code in deduped:
        if code.status != "not_found":
            continue
        cause = _cause_of(code)
        if cause == 1 and floor_members is not None and code.key in floor_members:
            cause = 2
        c1 += cause == 1
        c2 += cause == 2
        c3 += cause == 3
        c4 += cause == 4
    return FloorBand(cause1=c1, cause2=c2, cause3=c3, cause4=c4,
                     checkable=denom.checkable, not_found=denom.not_found)
