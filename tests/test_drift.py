"""P3-3 drift detection. Pure numerical and file-based: no API, no DB.

The controlled injected drop is the gate. The test that matters most for
honesty is the one on the two REAL windows, which asserts the module refuses
to attribute their delta to the model, for three separately named reasons.
"""
from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import numpy as np

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


def degrade(payload: dict, *, fraction: float, seed: int) -> dict:
    """Flip `found` off on a seeded share of reference facts.

    Lives here, not in governance/: production code should not ship a tool for
    making results worse. Flipping `found` lowers captured and
    correctly_placed together, so score_structuring's invariants hold and the
    degradation looks like a real capture regression rather than a corrupt
    artifact.
    """
    rng = np.random.default_rng(seed)
    out = copy.deepcopy(payload)
    for example in out["examples"]:
        for fact in example["ref"]:
            if fact["found"] and rng.random() < fraction:
                fact["found"] = False
    return out


@needs_windows
def test_an_artifact_against_itself_shows_no_drift():
    """Identical windows cancel exactly, because the indices are shared.

    Zero width here is the paired design working, not a degenerate interval:
    it is the same property tests/test_coding_bootstrap.py pins for two
    identical arms.
    """
    payload = json.loads(COMMITTED_AUG.read_text(encoding="utf-8"))
    result = compare_structuring_windows(payload, payload, controlled_pair=True,
                                         replicates=2000)
    assert result.verdict is DriftVerdict.NO_DRIFT
    assert result.delta == 0.0
    assert result.ci == (0.0, 0.0)
    assert result.mde == 0.0
    assert result.direction is None
    assert result.n_paired == 120


@needs_windows
def test_an_injected_drop_is_flagged():
    """The gate: a controlled degradation of window 2 must be flagged."""
    payload = json.loads(COMMITTED_AUG.read_text(encoding="utf-8"))
    degraded = degrade(payload, fraction=0.25, seed=11)

    result = compare_structuring_windows(payload, degraded,
                                         controlled_pair=True, replicates=2000)
    assert result.verdict is DriftVerdict.DRIFT
    assert result.direction == "degradation"
    assert result.delta < 0
    assert result.ci[1] < 0, "the interval must exclude zero on the losing side"
    assert result.mde > 0


@needs_windows
def test_an_injected_improvement_is_flagged_as_drift_not_hidden():
    """A significant move upward is still the model moving under a fixed config.

    P4-1 filters alerts on direction. The detector does not decide that for it,
    because a silent improvement is evidence the vendor changed something.
    """
    payload = json.loads(COMMITTED_AUG.read_text(encoding="utf-8"))
    degraded = degrade(payload, fraction=0.25, seed=11)

    result = compare_structuring_windows(degraded, payload,
                                         controlled_pair=True, replicates=2000)
    assert result.verdict is DriftVerdict.DRIFT
    assert result.direction == "improvement"
    assert result.delta > 0


@needs_windows
def test_the_two_real_windows_are_not_attributable():
    """Windows 7 and 25, the only real pair. Three reasons, all named.

    This is the module working, not failing. P3-2 recorded these three reasons
    in the roadmap; asserting them here is what stops a later reader "fixing"
    the refusal because the delta looked clean.
    """
    reference = json.loads(COMMITTED_JUL.read_text(encoding="utf-8"))
    current = json.loads(COMMITTED_AUG.read_text(encoding="utf-8"))

    result = compare_structuring_windows(reference, current, replicates=2000)

    assert result.verdict is DriftVerdict.NOT_ATTRIBUTABLE
    assert result.comparability == ("max_tokens",)
    assert set(result.unmeasured_variance) == {"generation_sampling", "instrument"}

    # The statistic is still reported. Refusing to attribute it is not the
    # same as refusing to measure it.
    assert result.delta is not None
    assert result.ci is not None
    assert result.mde > 0
    assert result.n_paired == 120

    joined = " ".join(result.caveats)
    assert "max_tokens" in joined
    assert "6550" in joined and "6553" in joined, "name the instrument's move"


@needs_windows
def test_a_differing_config_downgrades_even_a_null_result():
    """Provenance outranks the statistic in BOTH directions.

    Comparing two windows that were not generated the same way and finding no
    difference is not evidence of stability either, so it may not be reported
    as NO_DRIFT.
    """
    reference = json.loads(COMMITTED_AUG.read_text(encoding="utf-8"))
    current = copy.deepcopy(reference)
    current["structuring_max_tokens"] = 1200

    result = compare_structuring_windows(reference, current, replicates=500)
    assert result.delta == 0.0
    assert result.verdict is DriftVerdict.NOT_ATTRIBUTABLE
    assert result.comparability == ("max_tokens",)
