"""P2-4 note-level paired BCa bootstrap. Pure numerical, no API, no DB.

The load-bearing property (spec §4): with two identical arms, EVERY replicate
delta must be exactly 0, because both arms share the resampled indices. That is
what the shared-index design guarantees, and it is what an independent-resample
bug would break (widening the interval toward the branch this spec predicts).
"""
from __future__ import annotations

import numpy as np
import pytest

from governance.coding_bootstrap import (
    NotePair, ratio_diff, replicate_deltas, paired_bootstrap_bca,
)


def _pair(nf_a, ck_a, nf_b, ck_b):
    return NotePair(nf_a=nf_a, checkable_a=ck_a, nf_b=nf_b, checkable_b=ck_b)


def test_ratio_diff_is_a_difference_of_ratios_of_sums():
    # nf_a=3, ck_a=4 -> 0.75; nf_b=1, ck_b=5 -> 0.2; diff 0.55 (proportion).
    assert ratio_diff(3, 4, 1, 5) == pytest.approx(0.55)


def test_ratio_diff_is_none_on_zero_denominator():
    assert ratio_diff(0, 0, 1, 5) is None
    assert ratio_diff(3, 4, 0, 0) is None


def test_identical_arms_give_every_replicate_delta_exactly_zero():
    pairs = [_pair(1, 3, 1, 3), _pair(2, 4, 2, 4), _pair(0, 2, 0, 2)]
    rng = np.random.default_rng(123)
    deltas, dropped = replicate_deltas(pairs, rng, replicates=500)
    assert dropped == 0
    assert np.all(deltas == 0.0), "shared indices must cancel identical arms exactly"


def test_identical_arms_yield_a_zero_point_estimate_and_zero_width_ci():
    pairs = [_pair(1, 3, 1, 3), _pair(2, 4, 2, 4)]
    res = paired_bootstrap_bca(pairs, seed=7, replicates=500)
    assert res.d == pytest.approx(0.0)
    assert res.ci == pytest.approx((0.0, 0.0))
    assert res.acceleration_degenerate is True   # all jackknife reps equal -> 0/0
    assert res.acceleration == 0.0


def test_result_is_in_points_not_proportion():
    # Arm A all not_found, arm B all verified: proportion diff 1.0 -> 100 points.
    pairs = [_pair(2, 2, 0, 2), _pair(3, 3, 0, 3)]
    res = paired_bootstrap_bca(pairs, seed=1, replicates=500)
    assert res.d == pytest.approx(100.0)


def test_seed_and_replicate_count_are_recorded_and_reproducible():
    pairs = [_pair(1, 3, 0, 3), _pair(2, 4, 1, 4), _pair(0, 2, 1, 2)]
    a = paired_bootstrap_bca(pairs, seed=42, replicates=1000)
    b = paired_bootstrap_bca(pairs, seed=42, replicates=1000)
    assert a.seed == 42 and a.replicates == 1000
    assert a.ci == pytest.approx(b.ci)          # same seed -> same interval


def test_zero_denominator_replicates_are_dropped_and_counted():
    # One note has checkable 0 for arm A; some resamples draw only that note,
    # giving a None replicate that must be dropped, not treated as 0.
    pairs = [_pair(0, 0, 0, 1), _pair(1, 2, 0, 2)]
    res = paired_bootstrap_bca(pairs, seed=3, replicates=2000)
    assert res.dropped > 0
    assert res.retained + res.dropped == 2000
    assert res.retained > 0


def test_empty_denominator_point_estimate_raises():
    with pytest.raises(ValueError, match="zero checkable"):
        paired_bootstrap_bca([_pair(0, 0, 0, 0)], seed=1, replicates=10)


def test_acceleration_degenerate_flag_is_false_on_normal_data():
    pairs = [_pair(1, 3, 0, 3), _pair(2, 4, 1, 5), _pair(0, 2, 2, 4),
             _pair(3, 5, 1, 3)]
    res = paired_bootstrap_bca(pairs, seed=9, replicates=2000)
    assert res.acceleration_degenerate is False
