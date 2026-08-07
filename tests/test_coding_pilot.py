"""P2-4 pilot: train-split loader and diagnostics. No held-out data (spec §8)."""
from __future__ import annotations

from governance.coding_pilot import load_aci_train, pin_pilot_ids


def test_train_loader_returns_only_train_split_encounters():
    train = load_aci_train()
    assert len(train) == 67          # re-measured 2026-07-22
    ids = {e.encounter_id for e in train}
    # No held-out id may appear (the pilot must never touch the analysis set).
    from governance.heldout import load_aci_heldout
    heldout_ids = {e.encounter_id for e in load_aci_heldout()}
    assert ids.isdisjoint(heldout_ids)


def test_pilot_draw_is_pinned_and_reproducible():
    a = pin_pilot_ids(n=5, seed=20260722)
    b = pin_pilot_ids(n=5, seed=20260722)
    assert a == b and len(a) == 5    # cannot be redrawn until it gives a nicer answer


# ---------- pilot diagnostics (Task 7.2) ----------

import pytest  # noqa: E402
from governance.coding_pilot import (  # noqa: E402
    pearson_rho, equivalence_attainable, pilot_v_check,
)


def test_pearson_rho_of_identical_series_is_one():
    assert pearson_rho([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_pearson_rho_is_none_on_degenerate_input():
    assert pearson_rho([1.0], [1.0]) is None            # <2 points
    assert pearson_rho([2.0, 2.0], [1.0, 3.0]) is None  # zero variance one side


def test_equivalence_attainable_uses_the_se_diff_threshold():
    # DELTA/1.96 ~ 0.765 points. High rho shrinks SE_diff below it -> attainable.
    assert equivalence_attainable(se_arm_points=0.5, rho=0.9, n_pilot=5,
                                  n_target=120) is True
    # Low rho and a big SE keep SE_diff above the threshold -> not attainable.
    assert equivalence_attainable(se_arm_points=5.0, rho=0.0, n_pilot=5,
                                  n_target=120) is False


def test_equivalence_attainable_treats_none_rho_pessimistically():
    # A None rho must behave like rho=0 (the widest interval), never like a
    # helpful high correlation, or the pilot would greenlight a run that
    # cannot reach the Equivalent branch.
    assert (equivalence_attainable(se_arm_points=2.0, rho=None, n_pilot=5,
                                   n_target=120)
            == equivalence_attainable(se_arm_points=2.0, rho=0.0, n_pilot=5,
                                      n_target=120))


def test_pilot_v_check_flags_a_high_absolute_v():
    # v >= 98 in absolute terms escalates even inside the 5-point band (spec §4).
    assert pilot_v_check(v_pilot=98.5)["escalate"] is True
    assert pilot_v_check(v_pilot=94.0)["escalate"] is False
    # a >5 point deviation in either direction escalates
    assert pilot_v_check(v_pilot=88.0)["escalate"] is True
