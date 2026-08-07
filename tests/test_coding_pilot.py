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
