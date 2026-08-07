"""The P2-4 pre-registered decision rule and guards (spec §2). Pure.

Decides the QUALITY branch and names the lower-not-found arm on a Difference.
Cost routing under Equivalent/Inconclusive is applied by the caller with the
price table, because a missing price table is a terminal state (spec §8).

The literal wording is the rule. abs(d), not d. The Inconclusive branch is
present so an underpowered null and a genuine null do not collapse to the same
action. Intersection loss voids the run; the other guards return Inconclusive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Pre-registered constants (points except the two dimensionless ratios). Never
# recomputed from data; that would weaken the pre-registration (spec §4).
DELTA = 1.5                 # points
UNCHECKED_GUARD = 1.6       # points
VOLUME_GUARD = 0.25         # dimensionless ratio
INTERSECTION_FLOOR = 0.90   # dimensionless ratio, of the 120-note target
TARGET_NOTES = 120


class Branch(Enum):
    EQUIVALENT = "equivalent"
    DIFFERENCE = "difference"
    INCONCLUSIVE = "inconclusive"
    VOID = "void"


@dataclass(frozen=True)
class ArmGuardStats:
    unchecked_share: float      # points
    codes_per_note: float
    floor_lower: float          # points
    floor_upper: float          # points


@dataclass(frozen=True)
class Decision:
    branch: Branch
    guards_tripped: list[str] = field(default_factory=list)
    route_on: str | None = None          # "cost", "quality", or None (Void)
    winner_arm: str | None = None        # "A"/"B" on a Difference
    framing_disagreement: bool = False
    reason: str = ""


def _guards(arm_a: ArmGuardStats, arm_b: ArmGuardStats,
            n_analysis: int, abs_d: float) -> list[str]:
    tripped: list[str] = []

    # Intersection loss VOIDS; it is checked by the caller too, but naming it
    # here keeps the guard list complete.
    if n_analysis < INTERSECTION_FLOOR * TARGET_NOTES:
        tripped.append("intersection_loss")

    if abs(arm_a.unchecked_share - arm_b.unchecked_share) > UNCHECKED_GUARD:
        tripped.append("unchecked_divergence")

    mean_cpn = (arm_a.codes_per_note + arm_b.codes_per_note) / 2.0
    if mean_cpn > 0 and abs(arm_a.codes_per_note - arm_b.codes_per_note) / mean_cpn > VOLUME_GUARD:
        tripped.append("volume_divergence")

    max_gap = max(arm_a.floor_upper - arm_b.floor_lower,
                  arm_b.floor_upper - arm_a.floor_lower, 0.0)
    if max_gap > abs_d:
        tripped.append("floor_divergence")

    return tripped


def decide(*, d: float, ci: tuple[float, float],
           arm_a: ArmGuardStats, arm_b: ArmGuardStats,
           n_analysis: int, nf_rate_a: float, nf_rate_b: float,
           pessimistic_better_arm: str | None = None,
           standard_better_arm: str | None = None) -> Decision:
    """Apply the rule. All rate inputs are percentage points.

    `d` and `ci` are the paired not-found-rate difference nf(A)-nf(B), points.
    `nf_rate_a`/`nf_rate_b` route a Difference to the lower not-found arm.
    The optional *_better_arm let the caller surface a standard/pessimistic
    disagreement as the finding regardless of branch (spec §2).
    """
    lo, hi = ci
    abs_d = abs(d)
    framing_disagreement = (
        standard_better_arm is not None
        and pessimistic_better_arm is not None
        and standard_better_arm != pessimistic_better_arm)

    tripped = _guards(arm_a, arm_b, n_analysis, abs_d)

    if "intersection_loss" in tripped:
        return Decision(branch=Branch.VOID, guards_tripped=tripped,
                        route_on=None, framing_disagreement=framing_disagreement,
                        reason="analysis set below 90% of 120; no result to report")

    if tripped:
        return Decision(branch=Branch.INCONCLUSIVE, guards_tripped=tripped,
                        route_on="cost", framing_disagreement=framing_disagreement,
                        reason=f"guard tripped: {', '.join(tripped)}")

    if -DELTA < lo and hi < DELTA:
        return Decision(branch=Branch.EQUIVALENT, route_on="cost",
                        framing_disagreement=framing_disagreement,
                        reason="CI within (-delta, +delta); route on cost")

    excludes_zero = lo > 0 or hi < 0
    if excludes_zero and abs_d > DELTA:
        winner = "A" if nf_rate_a < nf_rate_b else "B"
        return Decision(branch=Branch.DIFFERENCE, route_on="quality",
                        winner_arm=winner,
                        framing_disagreement=framing_disagreement,
                        reason=f"CI excludes zero and |d|>{DELTA}; "
                               f"route to lower not-found arm {winner}")

    return Decision(branch=Branch.INCONCLUSIVE, route_on="cost",
                    framing_disagreement=framing_disagreement,
                    reason="neither equivalence nor difference resolved; "
                           "route on cost, quality comparison unresolved")
