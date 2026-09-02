"""P2-4's published routing numbers, recomputed from the committed artifact.

The paired BCa engine is shared with P3-3 drift detection. This pins the
numbers the coding routing decision rests on, so any change to the engine that
moves them fails CI instead of silently restating the decision.

Bit-identical equality, not pytest.approx: a refactor that is supposed to
preserve behavior either preserves it exactly or has changed the draw order,
and an approximate assertion would let the second one through.
"""
import json
from pathlib import Path

import pytest

from governance.coding_bootstrap import NotePair, paired_bootstrap_bca

ARTIFACT = (Path(__file__).resolve().parents[1] / "governance" /
            "eval_artifacts" / "coding_20260807T214249Z.json")

# scripts/run_coding_benchmark.py:61-62. Hardcoded rather than imported: the
# point is to pin what the committed run USED, so a later edit to those
# constants shows up here as a failure instead of being followed silently.
SEED, REPLICATES = 20260722, 10000


def _pairs_from_artifact(payload: dict) -> list[NotePair]:
    """Rebuild the analysis-set pairs from the stored per-note tallies.

    Sorted ids, matching assemble_run's sorted analysis ids. Pairing note i of
    arm A with note j of arm B would still produce a plausible number, just
    from the wrong comparison, which is the failure this ordering prevents.
    """
    a, b = payload["arms"]["A"]["notes"], payload["arms"]["B"]["notes"]
    return [NotePair(nf_a=a[i]["not_found"],
                     checkable_a=a[i]["verified"] + a[i]["not_found"],
                     nf_b=b[i]["not_found"],
                     checkable_b=b[i]["verified"] + b[i]["not_found"])
            for i in sorted(set(a) & set(b))]


@pytest.mark.skipif(not ARTIFACT.exists(),
                    reason="coding artifact not committed")
def test_committed_coding_artifact_reproduces_its_published_interval():
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    pairs = _pairs_from_artifact(payload)
    assert len(pairs) == payload["arms"]["A"]["n_notes"] == 113

    result = paired_bootstrap_bca(pairs, seed=SEED, replicates=REPLICATES)
    published = payload["comparison"]

    assert result.d == published["delta_points"]
    assert list(result.ci) == published["delta_ci95"]
    assert result.acceleration == published["bootstrap"]["acceleration"]
    assert result.retained == published["bootstrap"]["retained"]
    assert result.dropped == published["bootstrap"]["dropped"]
