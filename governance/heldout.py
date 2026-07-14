"""Load the frozen held-out set, refusing to run if the split has drifted.

Every number this project reports is computed on the held-out set defined in
shared/splits.py and frozen in scripts/heldout_split.lock.json. That lock is
only worth something if something actually checks it, so this module
recomputes the manifest and compares digests before it hands back a single
example.

A mismatch is a hard error, never a warning. Scoring against a drifted split
does not produce a slightly wrong number, it produces a meaningless one that
still looks perfectly plausible on a resume. Refusing to run is the only safe
behavior, and it is the failure mode P0-5 exists to prevent.

Nothing here ever returns a train or dev encounter. See docs/HELD-OUT-POLICY.md.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from shared.splits import (
    HELDOUT,
    SplitRecord,
    build_all_records,
    manifest_digest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
DEFAULT_LOCK_PATH = REPO_ROOT / "scripts" / "heldout_split.lock.json"

# Dataset identifiers written to eval_runs.dataset_ref. Versioned with the
# split salt, so a redraw cannot be confused with the set it replaced.
ACI_DATASET_REF = "aci-bench-heldout-v1"
PRIMOCK_DATASET_REF = "primock57-heldout-v1"


class SplitDriftError(RuntimeError):
    """The on-disk split no longer matches the committed lock file."""


@dataclass(frozen=True)
class HeldoutExample:
    """One held-out encounter: what goes in, and what it is scored against."""

    dataset: str
    encounter_id: str
    transcript: str                              # ACI dialogue; "" for PriMock57
    reference_note: str                          # the clinician-written note
    highlights: tuple[str, ...] = ()             # PriMock57 gold key concepts
    audio: tuple[Path, ...] = field(default=())  # PriMock57 doctor/patient wavs


def verify_split(lock_path: Path = DEFAULT_LOCK_PATH,
                 data_root: Path = DEFAULT_DATA_ROOT) -> list[SplitRecord]:
    """Recompute the split from local data and assert it matches the lock.

    Returns the full manifest so callers can also assert what is *not* in the
    held-out set. Raises SplitDriftError if the datasets on disk no longer
    produce the split that was frozen in P0-5.
    """
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    records = build_all_records(data_root)
    actual = manifest_digest(records)
    expected = lock["digest"]

    if actual != expected:
        raise SplitDriftError(
            f"Held-out split has drifted. The lock file records digest "
            f"{expected[:12]}... but the datasets on disk now produce "
            f"{actual[:12]}.... Refusing to score: any accuracy computed "
            f"against a drifted split is invalid, however plausible it looks. "
            f"If the redraw was deliberate, bump SALT in shared/splits.py and "
            f"regenerate the lock with scripts/build_heldout_split.py."
        )
    return records


def _heldout_ids(records: list[SplitRecord], dataset: str) -> set[str]:
    return {r.encounter_id for r in records
            if r.dataset == dataset and r.split == HELDOUT}


def load_aci_heldout(data_root: Path = DEFAULT_DATA_ROOT,
                     lock_path: Path = DEFAULT_LOCK_PATH) -> list[HeldoutExample]:
    """The 120 ACI-Bench held-out encounters: dialogue in, clinician note as gold.

    This is the set the headline structuring metric is measured on, because it
    is the only held-out data whose reference notes carry clinician-written
    section headers, which is what makes SOAP placement scorable at all.
    """
    records = verify_split(lock_path=lock_path, data_root=data_root)
    wanted = _heldout_ids(records, "aci-bench")

    challenge = data_root / "aci-bench" / "data" / "challenge_data"
    examples: list[HeldoutExample] = []
    seen: set[str] = set()

    for csv_path in sorted(challenge.glob("*.csv")):
        if csv_path.name.endswith("_metadata.csv"):
            continue
        with csv_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                eid = row["encounter_id"]
                if eid not in wanted or eid in seen:
                    continue
                seen.add(eid)
                examples.append(HeldoutExample(
                    dataset="aci-bench",
                    encounter_id=eid,
                    transcript=row["dialogue"],
                    reference_note=row["note"],
                ))

    missing = wanted - seen
    if missing:
        # A held-out encounter we cannot load is a silently shrunk denominator.
        raise SplitDriftError(
            f"{len(missing)} held-out ACI-Bench encounters are in the lock but "
            f"not on disk, e.g. {sorted(missing)[:3]}. Refusing to score a "
            f"partial held-out set.")

    examples.sort(key=lambda e: e.encounter_id)
    return examples


def load_primock_heldout(data_root: Path = DEFAULT_DATA_ROOT,
                         lock_path: Path = DEFAULT_LOCK_PATH
                         ) -> list[HeldoutExample]:
    """The 7 PriMock57 held-out consultations: audio in, GP note as gold.

    These carry the Phase 1 exit gate's end-to-end audio run. Their notes are
    free-text GP shorthand rather than SOAP sections, so they are scored on
    recall of the human-authored `highlights` and never on section placement.
    The transcript is empty here on purpose: it comes from Whisper at run time.
    """
    records = verify_split(lock_path=lock_path, data_root=data_root)
    wanted = _heldout_ids(records, "primock57")

    notes_dir = data_root / "primock57" / "notes"
    audio_dir = data_root / "primock57" / "audio"
    examples: list[HeldoutExample] = []

    for group_id in sorted(wanted):
        payload = json.loads(
            (notes_dir / f"{group_id}.json").read_text(encoding="utf-8"))
        audio = tuple(
            p for p in (audio_dir / f"{group_id}_doctor.wav",
                        audio_dir / f"{group_id}_patient.wav") if p.is_file())
        examples.append(HeldoutExample(
            dataset="primock57",
            encounter_id=group_id,
            transcript="",
            reference_note=payload["note"],
            highlights=tuple(payload.get("highlights", ())),
            audio=audio,
        ))

    return examples
