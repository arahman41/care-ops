"""P2-4 pilot. Drawn from the ACI train split, never the held-out set: proceeding
after seeing held-out outcomes would be optional stopping inside the analysis set
(spec §8). heldout.py exposes no train path on purpose, so the loader lives here.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from governance.heldout import DEFAULT_DATA_ROOT
from shared.splits import TRAIN, build_all_records

REPO_ROOT = Path(__file__).resolve().parents[1]


class TrainExample:
    __slots__ = ("encounter_id", "reference_note")

    def __init__(self, encounter_id: str, reference_note: str):
        self.encounter_id = encounter_id
        self.reference_note = reference_note


def load_aci_train(data_root: Path = DEFAULT_DATA_ROOT) -> list[TrainExample]:
    """The ACI train-split encounters, reference note only. No split verification
    guard here (that guards the held-out set); this reads train rows the pilot is
    allowed to see."""
    records = build_all_records(data_root)
    wanted = {r.encounter_id for r in records
              if r.dataset == "aci-bench" and r.split == TRAIN}

    challenge = data_root / "aci-bench" / "data" / "challenge_data"
    out: list[TrainExample] = []
    seen: set[str] = set()
    for csv_path in sorted(challenge.glob("*.csv")):
        if csv_path.name.endswith("_metadata.csv"):
            continue
        with csv_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                eid = row["encounter_id"]
                if eid in wanted and eid not in seen:
                    seen.add(eid)
                    out.append(TrainExample(eid, row["note"]))
    out.sort(key=lambda e: e.encounter_id)
    return out


def pin_pilot_ids(n: int, seed: int,
                  data_root: Path = DEFAULT_DATA_ROOT) -> list[str]:
    """A deterministic, recorded draw of n train ids, so the pilot cannot be
    redrawn until it gives a preferred answer (spec §8)."""
    ids = [e.encounter_id for e in load_aci_train(data_root)]
    # sha256-ranked, seed-salted: stable across processes and OSes.
    ranked = sorted(ids, key=lambda i: hashlib.sha256(
        f"{seed}:{i}".encode("utf-8")).hexdigest())
    return sorted(ranked[:n])
