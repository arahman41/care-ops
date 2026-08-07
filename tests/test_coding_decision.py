"""P2-4 pre-registered decision rule. Pure. The literal wording is the rule
(spec §2): abs(d), the Inconclusive branch, guards first, intersection loss
voids rather than returning Inconclusive.
"""
from __future__ import annotations

from governance.coding_decision import (
    DELTA, ArmGuardStats, decide, Branch,
)


def _stats(unchecked_share, codes_per_note, floor_lower, floor_upper):
    return ArmGuardStats(unchecked_share=unchecked_share,
                         codes_per_note=codes_per_note,
                         floor_lower=floor_lower, floor_upper=floor_upper)


# Two well-behaved arms whose guards never trip for the branch tests. Floors are
# TIGHT (gap 0.1) so max_possible_floor_gap stays under the small |d| used below;
# a wide floor band would trip the floor guard and force Inconclusive, masking the
# branch logic these tests exist to exercise.
A = _stats(unchecked_share=20.0, codes_per_note=5.0, floor_lower=5.9, floor_upper=6.0)
B = _stats(unchecked_share=20.5, codes_per_note=5.1, floor_lower=5.9, floor_upper=6.0)

# Equal, zero-width floors: max_possible_floor_gap is exactly 0, so the floor
# guard cannot trip even at d == 0. Used only by the scale test.
FLAT = _stats(unchecked_share=20.0, codes_per_note=5.0, floor_lower=6.0, floor_upper=6.0)


def test_delta_is_the_pre_registered_points_constant():
    assert DELTA == 1.5


def test_ci_within_margin_is_equivalent_route_on_cost():
    r = decide(d=0.2, ci=(-1.0, 1.0), arm_a=A, arm_b=B, n_analysis=120,
               nf_rate_a=6.0, nf_rate_b=6.2)
    assert r.branch is Branch.EQUIVALENT
    assert r.route_on == "cost"


def test_ci_excludes_zero_and_abs_d_over_delta_is_difference():
    # d = -5 (arm B lower not-found), CI clear of zero and |d|>1.5 -> Difference,
    # route to the lower-not-found arm (B).
    r = decide(d=-5.0, ci=(-7.0, -3.0), arm_a=A, arm_b=B, n_analysis=120,
               nf_rate_a=11.0, nf_rate_b=6.0)
    assert r.branch is Branch.DIFFERENCE
    assert r.winner_arm == "B"          # lower not-found rate
    assert r.route_on == "quality"


def test_uses_abs_d_not_d_so_a_clear_arm_b_win_is_a_difference():
    # An earlier draft wrote 'point estimate exceeds delta', sending d=-5 to
    # Inconclusive. abs(d) is the rule.
    r = decide(d=-5.0, ci=(-7.0, -3.0), arm_a=A, arm_b=B, n_analysis=120,
               nf_rate_a=11.0, nf_rate_b=6.0)
    assert r.branch is Branch.DIFFERENCE


def test_ci_straddling_zero_but_wide_is_inconclusive():
    r = decide(d=0.5, ci=(-2.0, 3.0), arm_a=A, arm_b=B, n_analysis=120,
               nf_rate_a=6.0, nf_rate_b=5.5)
    assert r.branch is Branch.INCONCLUSIVE
    assert r.route_on == "cost"


def test_a_points_scale_ci_of_three_points_is_not_equivalent():
    # Guards the scale (spec §2). A real ±3-point CI is NOT within ±1.5, so it is
    # not Equivalent. This passes ONLY because DELTA is points; if DELTA were
    # read as a proportion (1.5 ~ 150 points) the CI would fit and wrongly
    # return Equivalent, which is the 'every run returns Equivalent' failure.
    # FLAT arms keep the floor guard from tripping at d==0, so the branch logic
    # (not a guard) decides the outcome.
    r = decide(d=0.0, ci=(-3.0, 3.0), arm_a=FLAT, arm_b=FLAT, n_analysis=120,
               nf_rate_a=6.0, nf_rate_b=6.0)
    assert r.branch is not Branch.EQUIVALENT


# ---------- guards evaluate first and force Inconclusive ----------

def test_unchecked_divergence_guard_forces_inconclusive():
    # Tight, equal floors so ONLY the unchecked guard trips, not the floor guard.
    a = _stats(10.0, 5.0, 5.9, 6.0)
    b = _stats(12.0, 5.0, 5.9, 6.0)      # unchecked gap 2.0 > 1.6
    r = decide(d=0.2, ci=(-1.0, 1.0), arm_a=a, arm_b=b, n_analysis=120,
               nf_rate_a=6.0, nf_rate_b=6.2)
    assert r.branch is Branch.INCONCLUSIVE
    assert "unchecked_divergence" in r.guards_tripped
    assert r.route_on == "cost"


def test_volume_divergence_guard_is_symmetric_on_the_mean():
    # cpn 4 vs 6: |4-6| / ((4+6)/2) = 2/5 = 0.4 > 0.25. Tight equal floors keep
    # the floor guard quiet so only the volume guard is under test.
    a = _stats(20.0, 4.0, 5.9, 6.0)
    b = _stats(20.0, 6.0, 5.9, 6.0)
    r = decide(d=0.2, ci=(-1.0, 1.0), arm_a=a, arm_b=b, n_analysis=120,
               nf_rate_a=6.0, nf_rate_b=6.2)
    assert "volume_divergence" in r.guards_tripped


def test_floor_divergence_guard_uses_max_possible_gap():
    # max_possible_floor_gap = max(upper(A)-lower(B), upper(B)-lower(A), 0).
    # A: lower 1, upper 9; B: lower 1, upper 6 -> gap max(9-1, 6-1, 0)=8 > |d|=2.
    a = _stats(20.0, 5.0, 1.0, 9.0)
    b = _stats(20.0, 5.0, 1.0, 6.0)
    r = decide(d=-2.0, ci=(-3.5, -0.5), arm_a=a, arm_b=b, n_analysis=120,
               nf_rate_a=8.0, nf_rate_b=6.0)
    assert "floor_divergence" in r.guards_tripped
    assert r.branch is Branch.INCONCLUSIVE


def test_intersection_loss_voids_rather_than_inconclusive():
    r = decide(d=0.2, ci=(-1.0, 1.0), arm_a=A, arm_b=B, n_analysis=100,  # <108
               nf_rate_a=6.0, nf_rate_b=6.2)
    assert r.branch is Branch.VOID
    assert "intersection_loss" in r.guards_tripped


def test_intersection_at_exactly_the_floor_is_not_voided():
    # 90% of 120 = 108. The guard fires BELOW 108, so 108 is allowed.
    r = decide(d=0.2, ci=(-1.0, 1.0), arm_a=A, arm_b=B, n_analysis=108,
               nf_rate_a=6.0, nf_rate_b=6.2)
    assert r.branch is not Branch.VOID
