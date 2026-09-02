"""P3-3: drift between two measurement windows.

A window is a point in time with the generation configuration held FIXED
(P3-1), so a change between two windows is change on the vendor's side of a
hosted model. This module decides whether such a change is visible above
sampling noise, and, separately, whether it may be ATTRIBUTED to the model at
all.

Those two questions are not the same, and conflating them is the failure this
module is built to prevent. P3-2 measured window 2 and found its f1 moved
+0.005208 against window 1, then recorded three reasons that delta is not yet
drift: the two windows are not certified comparable (max_tokens was never
recorded in July), the generation-sampling baseline is unmeasured, and the
measuring instrument moved too (6,553 reference facts against 6,550, from
byte-identical reference notes). Those reasons live here as behavior now,
rather than as prose in the roadmap that a later reader may not find.

Accuracy uses a PAIRED bootstrap because both windows score the same held-out
encounters. Evidently's two-sample presets treat the windows as independent
and discard that pairing, which is where their power goes on an n of 120.
Evidently is still the right tool for the confidence stream, where two time
ranges genuinely are independent samples; see compare_confidence.

There is no boolean in the return type. A caller cannot read past the caveats
to a bare "drift: yes/no", which is the same structural refusal P3-1 uses to
stop an unscoreable agent being written an accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import numpy as np

from governance.bootstrap import BcaResult, paired_bca
from governance.eval_runner import config_from_artifact
from governance.evaluate import StructuringCounts, score_structuring
from governance.structuring_eval import per_encounter_counts

# Fixed so a drift verdict is reproducible from the artifacts alone. Changing
# it changes published intervals, so it is a constant, not a default argument
# somebody tunes until the answer looks better.
DRIFT_SEED = 20260902
DRIFT_REPLICATES = 10000

# The order score_structuring's counts are summed in. Kept next to _matrix,
# which depends on it positionally.
_COUNT_FIELDS = ("ref_facts", "captured", "correctly_placed", "gen_facts",
                 "supported")


class DriftVerdict(Enum):
    """What may be said about a pair of windows.

    NO_DRIFT and DRIFT are statistical statements. NOT_ATTRIBUTABLE and
    NOT_COMPARABLE are statements about provenance, and they outrank the
    statistics: a delta that cannot be assigned to the model is not drift
    however clean its interval looks.
    """

    NO_DRIFT = "no_drift"
    DRIFT = "drift"
    NOT_ATTRIBUTABLE = "not_attributable"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True)
class DriftResult:
    verdict: DriftVerdict
    metric: str
    reference: float | None
    current: float | None
    delta: float | None            # current minus reference: negative is worse
    ci: tuple[float, float] | None
    mde: float | None              # CI half-width: what this could have detected
    direction: str | None          # "degradation" | "improvement" | None
    n_paired: int
    comparability: tuple[str, ...]         # GenerationConfig.differing_fields
    unmeasured_variance: tuple[str, ...]
    caveats: tuple[str, ...]
    bootstrap: BcaResult | None


def _structural_mismatches(reference: Mapping, current: Mapping,
                           ref_ids: set[str], cur_ids: set[str]) -> list[str]:
    """Reasons the two payloads are not two measurements of one thing.

    Checked before any arithmetic. Quietly inner-joining two different
    encounter sets would produce a plausible number for a comparison nobody
    made.
    """
    out: list[str] = []
    if reference.get("dataset_ref") != current.get("dataset_ref"):
        out.append(
            f"different dataset_ref: {reference.get('dataset_ref')!r} against "
            f"{current.get('dataset_ref')!r}")
    if reference.get("split_digest") != current.get("split_digest"):
        out.append(
            f"different split_digest: {str(reference.get('split_digest'))[:12]}"
            f"... against {str(current.get('split_digest'))[:12]}.... Two "
            f"windows scored on different held-out sets are not a trend")
    if ref_ids != cur_ids:
        only_ref, only_cur = ref_ids - cur_ids, cur_ids - ref_ids
        out.append(
            f"encounter sets differ: {len(only_ref)} only in the reference, "
            f"{len(only_cur)} only in the current window. The paired test "
            f"requires the same encounters in both")
    return out


def _not_comparable(metric: str, reasons: list[str], n_paired: int,
                    comparability: tuple[str, ...] = ()) -> DriftResult:
    return DriftResult(
        verdict=DriftVerdict.NOT_COMPARABLE, metric=metric, reference=None,
        current=None, delta=None, ci=None, mde=None, direction=None,
        n_paired=n_paired, comparability=comparability,
        unmeasured_variance=(), caveats=tuple(reasons), bootstrap=None)


def compare_structuring_windows(
        reference: Mapping, current: Mapping, *, metric: str = "f1",
        seed: int = DRIFT_SEED, replicates: int = DRIFT_REPLICATES,
        controlled_pair: bool = False) -> DriftResult:
    """Compare two structuring windows on one metric.

    `controlled_pair` asserts that the current payload is a DETERMINISTIC
    transformation of the reference payload, so generation-sampling and
    instrument variance are zero by construction. It is never true of two real
    runs, and the only caller that may pass it is a controlled test that built
    the current window itself.
    """
    ref_counts = per_encounter_counts(reference)
    cur_counts = per_encounter_counts(current)
    ref_ids, cur_ids = set(ref_counts), set(cur_counts)

    reasons = _structural_mismatches(reference, current, ref_ids, cur_ids)
    if reasons:
        return _not_comparable(metric, reasons, len(ref_ids & cur_ids))

    raise NotImplementedError("the statistic lands in the next task")
