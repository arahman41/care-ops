"""Leak-free, deterministic train/dev/held-out split definition.

This is the single source of truth for how every labeled encounter is
assigned to a split. The held-out split is frozen: it is used only to
measure accuracy and drift, and never to tune prompts, rules, thresholds,
model choice, or few-shot examples. See docs/HELD-OUT-POLICY.md.

Why this is correctness-critical: a leak here silently invalidates every
downstream metric. Two guarantees make the split trustworthy.

1. Determinism. Assignment is a pure function of a stable identifier. For
   PriMock57 we bucket sha256(SALT + group_id); sha256 is identical across
   processes and operating systems, unlike Python's builtin hash(), which
   is per-process salted and would leak data over time. For ACI-Bench we
   honor the published official split, so results stay comparable and the
   benchmark authors' patient-level grouping is preserved.

2. No cross-split leakage. Assignment keys on the encounter group, never
   on an individual file. A PriMock57 consultation owns a doctor file and
   a patient file; both normalize to one group id, so they can never land
   in different splits. ACI-Bench official splits are disjoint by
   encounter_id (verified in tests).

The split is versioned by SALT. Redrawing it is a deliberate, documented
event (bump to v2), never an accident.
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# Version tag for this split. Changing it redraws every PriMock57
# assignment and is a deliberate, logged decision, not a routine edit.
SALT = "care-ops-copilot/heldout/v1"

# Split names. dev is the only surface where tuning is allowed.
TRAIN = "train"
DEV = "dev"
HELDOUT = "heldout"

# PriMock57 bucket thresholds over sha256 % 100. ~20% held-out, ~20% dev,
# ~60% train. ACI-Bench ignores these and uses its official split.
_HELDOUT_MAX = 20            # buckets [0, 20)   -> heldout
_DEV_MAX = 40                # buckets [20, 40)  -> dev
                             # buckets [40, 100) -> train

# ACI-Bench official split file -> our split. Honoring this keeps the
# published test sets held out and preserves the authors' grouping.
ACI_OFFICIAL_TO_SPLIT = {
    "train": TRAIN,
    "valid": DEV,
    "clinicalnlp_taskB_test1": HELDOUT,
    "clinicalnlp_taskC_test2": HELDOUT,
    "clef_taskC_test3": HELDOUT,
}

_PRIMOCK_GROUP_RE = re.compile(r"^day\d+_consultation\d+$")


@dataclass(frozen=True)
class SplitRecord:
    """One labeled encounter's split assignment. Ids only, never content."""

    dataset: str          # "primock57" or "aci-bench"
    encounter_id: str     # group id, e.g. "day1_consultation01" or "D2N001"
    split: str            # TRAIN, DEV, or HELDOUT


def primock_group_id(filename: str) -> str:
    """Normalize any PriMock57 filename to its consultation group id.

    Doctor and patient files for one consultation must share a group so
    they can never split across sets. Raises on an unexpected shape rather
    than risk a silent misgroup, which would be a leak.

    >>> primock_group_id("day1_consultation01_doctor.wav")
    'day1_consultation01'
    >>> primock_group_id("day1_consultation01.json")
    'day1_consultation01'
    """
    stem = Path(filename).stem                      # drop directory + extension
    stem = re.sub(r"_(doctor|patient)$", "", stem)  # drop speaker suffix
    if not _PRIMOCK_GROUP_RE.match(stem):
        raise ValueError(f"Unexpected PriMock57 identifier: {filename!r}")
    return stem


def _bucket(group_id: str) -> int:
    """Stable 0..99 bucket for a group id. sha256, never builtin hash()."""
    digest = hashlib.sha256(f"{SALT}:{group_id}".encode("utf-8")).hexdigest()
    return int(digest, 16) % 100


def assign_primock_split(group_id: str) -> str:
    """Deterministically assign one PriMock57 consultation to a split."""
    b = _bucket(group_id)
    if b < _HELDOUT_MAX:
        return HELDOUT
    if b < _DEV_MAX:
        return DEV
    return TRAIN


def assign_aci_split(official_split: str) -> str:
    """Map an ACI-Bench official split label to our split."""
    try:
        return ACI_OFFICIAL_TO_SPLIT[official_split]
    except KeyError as exc:
        raise ValueError(f"Unknown ACI-Bench split: {official_split!r}") from exc


def build_primock_records(primock_root: Path) -> list[SplitRecord]:
    """Enumerate PriMock57 consultations from the notes directory."""
    notes_dir = primock_root / "notes"
    records: list[SplitRecord] = []
    for note_path in sorted(notes_dir.glob("*.json")):
        group_id = primock_group_id(note_path.name)
        records.append(SplitRecord("primock57", group_id,
                                   assign_primock_split(group_id)))
    return records


def build_aci_records(aci_root: Path) -> list[SplitRecord]:
    """Enumerate ACI-Bench encounters from the official challenge CSVs."""
    challenge = aci_root / "data" / "challenge_data"
    records: list[SplitRecord] = []
    for official_split in sorted(ACI_OFFICIAL_TO_SPLIT):
        csv_path = challenge / f"{official_split}.csv"
        with csv_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                records.append(SplitRecord(
                    "aci-bench", row["encounter_id"],
                    assign_aci_split(official_split)))
    return records


def build_all_records(data_root: Path) -> list[SplitRecord]:
    """Build the full split manifest from local datasets, canonically sorted."""
    records = build_primock_records(data_root / "primock57")
    records += build_aci_records(data_root / "aci-bench")
    records.sort(key=lambda r: (r.dataset, r.encounter_id))
    _assert_disjoint(records)
    return records


def _assert_disjoint(records: list[SplitRecord]) -> None:
    """Fail loudly if any encounter is assigned to more than one split."""
    seen: dict[tuple[str, str], str] = {}
    for r in records:
        key = (r.dataset, r.encounter_id)
        if key in seen and seen[key] != r.split:
            raise ValueError(
                f"Leak: {key} in both {seen[key]!r} and {r.split!r}")
        seen[key] = r.split


def manifest_digest(records: list[SplitRecord]) -> str:
    """Order-independent sha256 over the split assignment.

    Canonicalizes by sorting, so filesystem enumeration order can never
    change the digest. This is the value pinned in the committed lock file.
    """
    canonical = "\n".join(
        f"{r.dataset},{r.encounter_id},{r.split}"
        for r in sorted(records, key=lambda r: (r.dataset, r.encounter_id))
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def split_counts(records: list[SplitRecord]) -> dict[str, dict[str, int]]:
    """Per-dataset, per-split counts for reporting and the lock file."""
    counts: dict[str, dict[str, int]] = {}
    for r in records:
        counts.setdefault(r.dataset, {TRAIN: 0, DEV: 0, HELDOUT: 0})
        counts[r.dataset][r.split] += 1
    return counts
