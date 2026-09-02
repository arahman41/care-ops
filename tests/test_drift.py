"""P3-3 drift detection. Pure numerical and file-based: no API, no DB.

The controlled injected drop is the gate. The test that matters most for
honesty is the one on the two REAL windows, which asserts the module refuses
to attribute their delta to the model, for three separately named reasons.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from governance.drift import (
    DriftResult,
    DriftVerdict,
    compare_structuring_windows,
)

ARTIFACTS = Path(__file__).resolve().parents[1] / "governance" / "eval_artifacts"
COMMITTED_JUL = ARTIFACTS / "structuring_aci-bench-heldout-v1_20260714T032403Z.json"
COMMITTED_AUG = ARTIFACTS / "structuring_aci-bench-heldout-v1_20260831T205449Z.json"

needs_windows = pytest.mark.skipif(
    not (COMMITTED_JUL.exists() and COMMITTED_AUG.exists()),
    reason="both committed ACI windows are required")


def _artifact(ids, *, split_digest="split-a", dataset_ref="aci-bench-heldout-v1",
              max_tokens=8000, model="claude-sonnet-5", effort="high",
              prompt="b7b42093e9a7", ref_facts=4, captured=4):
    """A minimal artifact payload with one encounter per id.

    ref_facts and captured drive recall directly, so a test can move the
    metric without needing a real run.
    """
    return {
        "dataset_ref": dataset_ref,
        "split_digest": split_digest,
        "structuring_model": model,
        "structuring_effort": effort,
        "structuring_max_tokens": max_tokens,
        "prompt_versions": {"structuring": prompt},
        "n_examples": len(ids),
        "examples": [
            {"encounter_id": i,
             "ref": [{"found": n < captured, "section": "assessment",
                      "acceptable": ["assessment"]} for n in range(ref_facts)],
             "gen": [True] * ref_facts}
            for i in ids],
    }


def test_mismatched_encounter_keysets_are_not_comparable():
    result = compare_structuring_windows(_artifact(["A", "B", "C"]),
                                         _artifact(["A", "B", "D"]))
    assert result.verdict is DriftVerdict.NOT_COMPARABLE
    assert result.delta is None, "nothing may be computed on an unpaired set"
    assert "encounter" in " ".join(result.caveats).lower()


def test_different_splits_are_not_comparable():
    result = compare_structuring_windows(
        _artifact(["A", "B"], split_digest="aaa"),
        _artifact(["A", "B"], split_digest="bbb"))
    assert result.verdict is DriftVerdict.NOT_COMPARABLE
    assert result.delta is None
    assert "split" in " ".join(result.caveats).lower()


def test_different_datasets_are_not_comparable():
    result = compare_structuring_windows(
        _artifact(["A", "B"], dataset_ref="aci-bench-heldout-v1"),
        _artifact(["A", "B"], dataset_ref="primock57-heldout-v1"))
    assert result.verdict is DriftVerdict.NOT_COMPARABLE
    assert result.delta is None


def test_result_exposes_no_boolean_drift_field():
    """A consumer must not be able to read past the caveats to a bool.

    P3-1 refuses an unscoreable agent the accuracy family structurally rather
    than by documentation. Same shape here: a dashboard cannot render "DRIFT"
    off a pair that is not attributable, because there is no boolean to read.
    """
    fields = {f.name: str(f.type) for f in dataclasses.fields(DriftResult)}
    assert "drift_detected" not in fields
    assert not [n for n, t in fields.items() if "bool" in t], fields
