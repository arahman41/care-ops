"""Tests for the leak-free held-out split (P0-5).

Most tests are hermetic: they exercise the pure split logic and need no
datasets present, so they run in CI. One integration test rebuilds the
real split from local data and is skipped when the datasets are absent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.splits import (
    DEV,
    HELDOUT,
    TRAIN,
    SplitRecord,
    _assert_disjoint,
    assign_aci_split,
    assign_primock_split,
    build_all_records,
    manifest_digest,
    primock_group_id,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"
LOCK_PATH = REPO_ROOT / "scripts" / "heldout_split.lock.json"


# ---------- grouping: the core leak guard ----------

def test_doctor_and_patient_share_one_group():
    # A consultation's doctor and patient files must never split apart.
    ids = {
        primock_group_id("day1_consultation01_doctor.wav"),
        primock_group_id("day1_consultation01_patient.wav"),
        primock_group_id("day1_consultation01_doctor.TextGrid"),
        primock_group_id("day1_consultation01.json"),
    }
    assert ids == {"day1_consultation01"}


def test_grouping_rejects_unexpected_identifier():
    # Fail loud rather than silently misgroup (a misgroup is a leak).
    with pytest.raises(ValueError):
        primock_group_id("random_file.wav")


# ---------- determinism: pinned assignments catch a hash() regression ----------

@pytest.mark.parametrize("group_id,expected", [
    ("day1_consultation01", TRAIN),
    ("day1_consultation02", DEV),
    ("day5_consultation12", DEV),
    ("day1_consultation03", HELDOUT),
])
def test_primock_assignment_is_pinned(group_id, expected):
    # These are frozen. If Python's per-process hash() ever crept in, or the
    # SALT changed, these would flip and the test would catch it.
    assert assign_primock_split(group_id) == expected


def test_primock_assignment_is_stable_across_calls():
    a = [assign_primock_split(f"day{d}_consultation{c:02d}")
         for d in range(1, 6) for c in range(1, 11)]
    b = [assign_primock_split(f"day{d}_consultation{c:02d}")
         for d in range(1, 6) for c in range(1, 11)]
    assert a == b


# ---------- ACI-Bench: honor the official split ----------

def test_aci_official_mapping():
    assert assign_aci_split("train") == TRAIN
    assert assign_aci_split("valid") == DEV
    assert assign_aci_split("clinicalnlp_taskB_test1") == HELDOUT
    assert assign_aci_split("clinicalnlp_taskC_test2") == HELDOUT
    assert assign_aci_split("clef_taskC_test3") == HELDOUT


def test_aci_unknown_split_raises():
    with pytest.raises(ValueError):
        assign_aci_split("some_new_test")


# ---------- integrity of the manifest ----------

def test_digest_is_order_independent():
    records = [
        SplitRecord("aci-bench", "D2N001", TRAIN),
        SplitRecord("primock57", "day1_consultation01", TRAIN),
        SplitRecord("aci-bench", "D2N088", HELDOUT),
    ]
    assert manifest_digest(records) == manifest_digest(list(reversed(records)))


def test_disjoint_detection_flags_a_leak():
    leaky = [
        SplitRecord("primock57", "day1_consultation01", TRAIN),
        SplitRecord("primock57", "day1_consultation01", HELDOUT),
    ]
    with pytest.raises(ValueError, match="Leak"):
        _assert_disjoint(leaky)


# ---------- integration: real split reproduces the committed lock ----------

_DATA_PRESENT = (DATA_ROOT / "primock57" / "notes").is_dir() and (
    DATA_ROOT / "aci-bench" / "data" / "challenge_data").is_dir()


@pytest.mark.skipif(not _DATA_PRESENT, reason="datasets not downloaded")
def test_real_split_reproduces_lock():
    records = build_all_records(DATA_ROOT)
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert manifest_digest(records) == lock["digest"]


@pytest.mark.skipif(not _DATA_PRESENT, reason="datasets not downloaded")
def test_heldout_covers_both_datasets():
    # The Phase 1 gate scores a PriMock57 held-out encounter, and ACI-Bench
    # supplies the large-N text held-out set. Both must be non-empty.
    records = build_all_records(DATA_ROOT)
    heldout = {r.dataset for r in records if r.split == HELDOUT}
    assert heldout == {"primock57", "aci-bench"}
