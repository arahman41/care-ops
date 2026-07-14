"""The harness must refuse to score against a drifted held-out split (P1-4).

P0-5 froze the split and committed its digest. That lock is only worth
anything if something actually checks it, so every number this project
reports is gated on `verify_split()` passing first. A drifted split does
not produce a slightly wrong number, it produces a meaningless one, so the
failure mode here is a hard raise and never a warning.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.heldout import (
    SplitDriftError,
    load_aci_heldout,
    load_primock_heldout,
    verify_split,
)
from shared.splits import HELDOUT

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"
LOCK_PATH = REPO_ROOT / "scripts" / "heldout_split.lock.json"

_DATA_PRESENT = (DATA_ROOT / "primock57" / "notes").is_dir() and (
    DATA_ROOT / "aci-bench" / "data" / "challenge_data").is_dir()

needs_data = pytest.mark.skipif(not _DATA_PRESENT,
                                reason="datasets not downloaded")


# ---------- the guard ----------

@needs_data
def test_verify_split_passes_against_the_committed_lock():
    records = verify_split(lock_path=LOCK_PATH, data_root=DATA_ROOT)
    assert any(r.split == HELDOUT for r in records)


@needs_data
def test_verify_split_raises_when_the_digest_does_not_match(tmp_path):
    # Simulates the split having been redrawn without the lock being updated.
    tampered = tmp_path / "heldout_split.lock.json"
    tampered.write_text(json.dumps({"digest": "0" * 64}), encoding="utf-8")
    with pytest.raises(SplitDriftError, match="drifted"):
        verify_split(lock_path=tampered, data_root=DATA_ROOT)


# ---------- loading: held-out only, never train or dev ----------

@needs_data
def test_aci_heldout_is_the_expected_size_and_shape():
    examples = load_aci_heldout(data_root=DATA_ROOT, lock_path=LOCK_PATH)
    assert len(examples) == 120                       # locked in P0-5
    assert all(e.dataset == "aci-bench" for e in examples)
    assert all(e.transcript.strip() for e in examples)
    assert all(e.reference_note.strip() for e in examples)


@needs_data
def test_primock_heldout_is_the_expected_size_and_carries_highlights():
    examples = load_primock_heldout(data_root=DATA_ROOT, lock_path=LOCK_PATH)
    assert len(examples) == 7                         # locked in P0-5
    assert all(e.dataset == "primock57" for e in examples)
    assert all(e.reference_note.strip() for e in examples)
    # The human-authored key concepts are the PriMock57 recall gold.
    assert sum(len(e.highlights) for e in examples) == 30


@needs_data
def test_no_train_or_dev_encounter_can_reach_the_scorer():
    # The whole point of the split. If this ever fails, every number is void.
    records = verify_split(lock_path=LOCK_PATH, data_root=DATA_ROOT)
    forbidden = {(r.dataset, r.encounter_id)
                 for r in records if r.split != HELDOUT}

    scored = load_aci_heldout(data_root=DATA_ROOT, lock_path=LOCK_PATH)
    scored += load_primock_heldout(data_root=DATA_ROOT, lock_path=LOCK_PATH)

    for e in scored:
        assert (e.dataset, e.encounter_id) not in forbidden
