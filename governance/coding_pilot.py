"""P2-4 pilot. Drawn from the ACI train split, never the held-out set: proceeding
after seeing held-out outcomes would be optional stopping inside the analysis set
(spec §8). heldout.py exposes no train path on purpose, so the loader lives here.
"""
from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

from governance.coding_decision import DELTA
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


# ---------- pilot diagnostics (pure) ----------

def pearson_rho(xs: list[float], ys: list[float]) -> float | None:
    """Between-arm correlation of per-note not-found rates. None when <2 points
    or either side has zero variance (rho is undefined, not 0)."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def equivalence_attainable(*, se_arm_points: float, rho: float | None,
                           n_pilot: int, n_target: int) -> bool:
    """Is the Equivalent branch reachable at n_target? Extrapolate the pilot's
    per-arm SE by the usual sqrt(n) scaling, form SE_diff = SE_arm*sqrt(2(1-rho))
    at the target n, and compare to DELTA/1.96 (spec §2). A None rho is treated
    as 0 (the pessimistic, wider-interval assumption)."""
    r = 0.0 if rho is None else rho
    se_arm_target = se_arm_points * math.sqrt(n_pilot / n_target)
    se_diff = se_arm_target * math.sqrt(2.0 * (1.0 - r))
    return se_diff < DELTA / 1.96


def pilot_v_check(v_pilot: float, projection: float = 94.0) -> dict:
    """Surface delta-sizing concerns to a human (spec §4). Never changes a
    threshold; delta stays the pre-registered 1.5."""
    escalate = abs(v_pilot - projection) > 5.0 or v_pilot >= 98.0
    return {"v_pilot": v_pilot, "projection": projection, "escalate": escalate}
