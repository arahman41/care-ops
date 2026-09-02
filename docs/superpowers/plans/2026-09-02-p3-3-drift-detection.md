# P3-3 Drift Detection Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `governance/drift.py` compares a reference window against a current window and flags an injected accuracy or confidence drop, without ever letting a consumer read a bare boolean off a pair that is not certified comparable.

**Architecture:** Accuracy drift is a paired BCa bootstrap over the 120 held-out encounters, reusing the engine `coding_bootstrap.py` already has and computing its statistic through the existing `score_structuring`, so no new metric arithmetic enters the codebase. Confidence drift keeps Evidently, where two time ranges genuinely are independent samples. The return type is a verdict enum plus named caveats, never a boolean.

**Tech Stack:** Python 3.10, numpy, scipy, evidently 0.5.1 (pinned), pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-p3-3-drift-detection-design.md`

**Branch:** `p3-3-drift-detection` (already created, spec committed at `3e48235`)

**Run tests with:** `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest <args>` from
`care-ops-copilot/`. The bare `pytest` in `make test` works too; the explicit
form is what these steps use because it does not depend on an activated venv.

---

## File Structure

| File | Responsibility |
|---|---|
| `governance/bootstrap.py` | **create.** Statistic-agnostic paired BCa engine. Pure numeric, no domain types. |
| `governance/coding_bootstrap.py` | **modify.** Keeps `NotePair`, `ratio_diff`, `BootResult`, `replicate_deltas`, `paired_bootstrap_bca`. Delegates the engine. Behavior frozen. |
| `governance/structuring_eval.py` | **modify.** Extract `per_encounter_counts` out of `replay`'s loop. |
| `governance/drift.py` | **rewrite.** `DriftVerdict`, `DriftResult`, `compare_structuring_windows`, `compare_confidence`. No DB, no network. |
| `scripts/run_drift_check.py` | **create.** CLI over two artifact paths or two `eval_runs` ids. DB is an index only. |
| `tests/test_bootstrap_regression.py` | **create.** Pins P2-4's published numbers through the engine. |
| `tests/test_drift.py` | **rewrite.** Replaces the single xfail. |

---

## Chunk 1: Freeze P2-4, then extract the engine

The extraction touches a file that protects a published routing decision, so
the pin goes in **first**, against the current code, and must stay green
through the refactor.

### Task 1: Pin P2-4's published numbers

**Files:**
- Test: `tests/test_bootstrap_regression.py` (create)

Verified 2026-09-02 against the committed artifact: 113 paired notes reproduce
`d`, both CI endpoints and the acceleration bit-identically.

- [ ] **Step 1: Write the test**

```python
"""P2-4's published routing numbers, recomputed from the committed artifact.

The paired BCa engine is shared with P3-3 drift detection. This pins the
numbers the coding routing decision rests on, so any change to the engine that
moves them fails CI instead of silently restating the decision.

Bit-identical equality, not approx: a refactor that is supposed to preserve
behavior either preserves it exactly or has changed the draw order.
"""
import json
from pathlib import Path

import pytest

from governance.coding_bootstrap import NotePair, paired_bootstrap_bca

ARTIFACT = (Path(__file__).resolve().parents[1] / "governance" /
            "eval_artifacts" / "coding_20260807T214249Z.json")

# scripts/run_coding_benchmark.py:61-62
SEED, REPLICATES = 20260722, 10000


def _pairs_from_artifact(payload: dict) -> list[NotePair]:
    """Rebuild the analysis-set pairs from the stored per-note tallies.

    Sorted ids, matching assemble_run's sorted analysis ids: pairing note i of
    arm A with note j of arm B would produce a plausible number from the wrong
    comparison.
    """
    a, b = payload["arms"]["A"]["notes"], payload["arms"]["B"]["notes"]
    return [NotePair(nf_a=a[i]["not_found"],
                     checkable_a=a[i]["verified"] + a[i]["not_found"],
                     nf_b=b[i]["not_found"],
                     checkable_b=b[i]["verified"] + b[i]["not_found"])
            for i in sorted(set(a) & set(b))]


@pytest.mark.skipif(not ARTIFACT.exists(), reason="coding artifact not committed")
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
```

- [ ] **Step 2: Run it against the CURRENT code**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_bootstrap_regression.py -v`
Expected: **PASS.** This is a characterization test, so it must pass before any
refactor. If it fails now, stop: the committed artifact and the current engine
already disagree, which is a finding that outranks this task.

- [ ] **Step 3: Commit**

```bash
git add tests/test_bootstrap_regression.py
git commit -m "test(P3-3): pin P2-4's published interval before touching the engine"
```

### Task 2: Extract the statistic-agnostic engine

**Files:**
- Create: `governance/bootstrap.py`
- Modify: `governance/coding_bootstrap.py`

- [ ] **Step 1: Write `governance/bootstrap.py`**

Move the numerics verbatim. The **draw order must not change**: the point
estimate is computed before the generator is constructed, and each replicate
draws exactly one `rng.integers(0, n, size=n)`.

```python
"""Paired BCa bootstrap, statistic-agnostic.

Extracted from coding_bootstrap.py so P2-4's coding comparison and P3-3's
drift detection share one implementation. Two hand-rolled BCa engines in one
repo would be free to diverge, and the two published numbers would stop being
comparable with nothing to detect it.

The caller supplies stat(idx) -> float | None over a resample index. None means
the replicate is undefined (a zero denominator) and is dropped, never coerced
to 0.0. BCa is hand-rolled because scipy's uses the naive strict-< bias
correction and does not implement the mid-rank tie convention pinned here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.stats import norm

Stat = Callable[[np.ndarray], float | None]


@dataclass(frozen=True)
class BcaResult:
    """Native scale throughout. Callers rescale at their own boundary."""
    d: float
    ci: tuple[float, float]
    seed: int
    replicates: int
    retained: int
    dropped: int
    acceleration: float
    acceleration_degenerate: bool


def replicate_deltas(n: int, stat: Stat, rng: np.random.Generator,
                     replicates: int) -> tuple[np.ndarray, int]:
    """Replicate statistics with ONE shared index per replicate.

    Shared, not independent per arm: the pairing is the whole point. Drawing
    separately would discard it and inflate the interval.
    """
    kept: list[float] = []
    dropped = 0
    for _ in range(replicates):
        s = stat(rng.integers(0, n, size=n))
        if s is None:
            dropped += 1
        else:
            kept.append(s)
    return np.array(kept, float), dropped


def paired_bca(*, n: int, stat: Stat, seed: int, replicates: int = 10000,
               alpha: float = 0.05) -> BcaResult:
    """BCa interval on a paired difference. Native scale."""
    full = np.arange(n)

    d = stat(full)
    if d is None:
        raise ValueError("the statistic is undefined on the full sample; "
                         "no point estimate")

    rng = np.random.default_rng(seed)
    deltas, dropped = replicate_deltas(n, stat, rng, replicates)
    B = len(deltas)
    if B == 0:
        raise ValueError("every bootstrap replicate was dropped (zero denom)")

    # z0: mid-rank tie convention over the RETAINED replicates.
    less = float(np.sum(deltas < d))
    eq = float(np.sum(deltas == d))
    z0 = norm.ppf((less + 0.5 * eq) / B)

    # Acceleration: leave-one-out jackknife.
    jack = [stat(np.delete(full, i)) for i in range(n)]
    jack = np.array([j for j in jack if j is not None], float)
    u = jack.mean() - jack
    num = float(np.sum(u ** 3))
    den = 6.0 * float(np.sum(u ** 2)) ** 1.5
    if den == 0.0:
        a, a_degenerate = 0.0, True
    else:
        a, a_degenerate = num / den, False

    def _adj(z_alpha: float) -> float:
        num_z = z0 + z_alpha
        return float(norm.cdf(z0 + num_z / (1.0 - a * num_z)))

    lo = float(np.quantile(deltas, _adj(norm.ppf(alpha / 2.0)), method="linear"))
    hi = float(np.quantile(deltas, _adj(norm.ppf(1.0 - alpha / 2.0)),
                           method="linear"))

    return BcaResult(d=float(d), ci=(lo, hi), seed=seed, replicates=replicates,
                     retained=B, dropped=dropped, acceleration=a,
                     acceleration_degenerate=a_degenerate)
```

- [ ] **Step 2: Rewrite `coding_bootstrap.py` to delegate**

Keep every existing public name. `tests/test_coding_bootstrap.py` imports
`NotePair`, `ratio_diff`, `replicate_deltas` and `paired_bootstrap_bca`, so all
four keep their current signatures. `_stat` and `_arrays` stay. The body of
`paired_bootstrap_bca` becomes:

```python
def _stat_fn(pairs: list[NotePair]) -> tuple[int, Stat]:
    nf_a, ck_a, nf_b, ck_b = _arrays(pairs)
    return len(pairs), lambda idx: _stat(nf_a, ck_a, nf_b, ck_b, idx)


def replicate_deltas(pairs: list[NotePair], rng: np.random.Generator,
                     replicates: int) -> tuple[np.ndarray, int]:
    """Unchanged signature, kept for tests/test_coding_bootstrap.py."""
    n, stat = _stat_fn(pairs)
    return engine_replicate_deltas(n, stat, rng, replicates)


def paired_bootstrap_bca(pairs: list[NotePair], seed: int,
                         replicates: int = 10000,
                         alpha: float = 0.05) -> BootResult:
    """95% BCa interval on the paired not-found-rate difference, in points."""
    n, stat = _stat_fn(pairs)
    r = paired_bca(n=n, stat=stat, seed=seed, replicates=replicates, alpha=alpha)
    return BootResult(d=100.0 * r.d, ci=(100.0 * r.ci[0], 100.0 * r.ci[1]),
                      seed=r.seed, replicates=r.replicates, retained=r.retained,
                      dropped=r.dropped, acceleration=r.acceleration,
                      acceleration_degenerate=r.acceleration_degenerate)
```

Import as `from governance.bootstrap import (Stat, paired_bca,
replicate_deltas as engine_replicate_deltas)`.

Note the one behavioral difference to check: the old code raised
`"analysis set has zero checkable codes for an arm; no point estimate"`. The
engine raises a generic message. If any test asserts on that string, keep the
domain message by catching and re-raising in `paired_bootstrap_bca`.

- [ ] **Step 3: Run the pin and the existing bootstrap tests**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_bootstrap_regression.py tests/test_coding_bootstrap.py tests/test_coding_benchmark.py -v`
Expected: **all PASS**, with `d`, both CI endpoints and acceleration
bit-identical. Any failure here means the draw order moved; fix it rather than
loosening the assertion.

- [ ] **Step 4: Lint**

Run: `make lint`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add governance/bootstrap.py governance/coding_bootstrap.py
git commit -m "refactor(P3-3): one paired BCa engine, shared by coding and drift"
```

### Task 3: Per-encounter counts out of `replay`

**Files:**
- Modify: `governance/structuring_eval.py` (the loop inside `replay`, around line 550)
- Test: `tests/test_structuring_eval.py`

- [ ] **Step 1: Write the failing test**

```python
def test_per_encounter_counts_sum_to_the_replayed_total():
    """Drift reads its observations from the same verdicts replay scores.

    If these ever diverge, drift is measuring something the headline is not.
    """
    payload = json.loads(COMMITTED_ACI.read_text(encoding="utf-8"))
    per = per_encounter_counts(payload)

    assert len(per) == payload["n_examples"] == 120
    total = sum(per.values(), StructuringCounts(0, 0, 0, 0, 0))
    assert total.__dict__ == payload["counts"]
```

- [ ] **Step 2: Run it**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_structuring_eval.py -k per_encounter -v`
Expected: FAIL, `ImportError: cannot import name 'per_encounter_counts'`.

- [ ] **Step 3: Extract the function**

```python
def per_encounter_counts(payload: Mapping) -> dict[str, StructuringCounts]:
    """The five tallies per encounter, keyed by encounter_id.

    replay() sums these. P3-3 pairs them across two windows. One parser of the
    artifact format, so drift cannot measure a different quantity from the one
    the headline is recomputed from.
    """
    out: dict[str, StructuringCounts] = {}
    for ex in payload["examples"]:
        ref, gen = ex["ref"], ex["gen"]
        out[ex["encounter_id"]] = StructuringCounts(
            ref_facts=len(ref),
            captured=sum(1 for f in ref if f["found"]),
            correctly_placed=sum(
                1 for f in ref if f["found"] and f["section"] in f["acceptable"]),
            gen_facts=len(gen),
            supported=sum(g["supported"] if isinstance(g, dict) else bool(g)
                          for g in gen),
        )
    return out
```

Then `replay` builds `total = sum(per_encounter_counts(payload).values(),
StructuringCounts(0, 0, 0, 0, 0))` and the rest of it is unchanged.

- [ ] **Step 4: Run the structuring tests**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_structuring_eval.py -v`
Expected: PASS, including the existing committed-artifact replay tests.

- [ ] **Step 5: Commit**

```bash
git add governance/structuring_eval.py tests/test_structuring_eval.py
git commit -m "refactor(P3-3): per-encounter counts, so drift and the headline read one parser"
```

---

## Chunk 2: The drift core

### Task 4: Verdict types and structural refusal

**Files:**
- Modify: `governance/drift.py` (replace the whole file)
- Test: `tests/test_drift.py` (replace the whole file)

- [ ] **Step 1: Write the failing tests**

```python
def test_mismatched_encounter_keysets_are_not_comparable():
    ref = _artifact(["A", "B", "C"])
    cur = _artifact(["A", "B", "D"])
    result = compare_structuring_windows(ref, cur)
    assert result.verdict is DriftVerdict.NOT_COMPARABLE
    assert result.delta is None          # nothing was computed
    assert "encounter" in " ".join(result.caveats).lower()


def test_different_splits_are_not_comparable():
    ref = _artifact(["A", "B"], split_digest="aaa")
    cur = _artifact(["A", "B"], split_digest="bbb")
    assert (compare_structuring_windows(ref, cur).verdict
            is DriftVerdict.NOT_COMPARABLE)


def test_result_exposes_no_boolean_drift_field():
    """A consumer must not be able to read past the caveats to a bool.

    This is the same shape as P3-1 refusing an unscoreable agent the accuracy
    family: the guarantee is structural, not documentary.
    """
    fields = {f.name: f.type for f in dataclasses.fields(DriftResult)}
    assert not any("bool" in str(t) for t in fields.values())
    assert "drift_detected" not in fields
```

`_artifact(...)` is a local helper building a minimal payload dict with
`examples`, `split_digest`, `dataset_ref`, `structuring_model`,
`prompt_versions`, `structuring_effort`, `structuring_max_tokens`.

- [ ] **Step 2: Run them**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_drift.py -v`
Expected: FAIL on import.

- [ ] **Step 3: Implement types and the structural gate**

```python
class DriftVerdict(Enum):
    NO_DRIFT = "no_drift"
    DRIFT = "drift"
    NOT_ATTRIBUTABLE = "not_attributable"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True)
class DriftResult:
    verdict: DriftVerdict
    metric: str
    reference: float | None
    current: float | None
    delta: float | None
    ci: tuple[float, float] | None
    mde: float | None                 # CI half-width: what this could detect
    direction: str | None             # "degradation" | "improvement" | None
    n_paired: int
    comparability: tuple[str, ...]    # GenerationConfig.differing_fields
    unmeasured_variance: tuple[str, ...]
    caveats: tuple[str, ...]
    bootstrap: BcaResult | None
```

Structural gate first, before any arithmetic: compare `dataset_ref`,
`split_digest`, and the `per_encounter_counts` keysets. On any mismatch return
`NOT_COMPARABLE` with `delta=None` and a caveat naming what differed.

- [ ] **Step 4: Run, expect PASS. Then commit**

```bash
git add governance/drift.py tests/test_drift.py
git commit -m "feat(P3-3): drift verdict types, and the structural refusal"
```

### Task 5: The paired statistic

**Files:**
- Modify: `governance/drift.py`
- Test: `tests/test_drift.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_an_artifact_against_itself_shows_no_drift():
    payload = json.loads(COMMITTED_AUG.read_text(encoding="utf-8"))
    r = compare_structuring_windows(payload, payload, controlled_pair=True,
                                    replicates=2000)
    assert r.delta == 0.0
    assert r.verdict is DriftVerdict.NO_DRIFT
    assert r.mde > 0            # "no change larger than this", never "no change"


def test_an_injected_drop_is_flagged():
    """The gate. A seeded degradation of window 2 must be flagged."""
    payload = json.loads(COMMITTED_AUG.read_text(encoding="utf-8"))
    degraded = degrade(payload, fraction=0.25, seed=11)
    r = compare_structuring_windows(payload, degraded, controlled_pair=True,
                                    replicates=2000)
    assert r.verdict is DriftVerdict.DRIFT
    assert r.direction == "degradation"
    assert r.ci[1] < 0          # interval excludes zero, on the losing side
```

`degrade` lives in the test file, not in `governance/`: production code should
not ship a tool for making results worse. It flips `found` from True to False
on a seeded subset of reference facts, which lowers `captured` and
`correctly_placed` together and so leaves the `score_structuring` invariants
intact.

- [ ] **Step 2: Run them, expect FAIL**

- [ ] **Step 3: Implement the statistic**

Vectorize the counts, or 10000 replicates over 120 dataclasses will crawl:

```python
_FIELDS = ("ref_facts", "captured", "correctly_placed", "gen_facts", "supported")


def _matrix(per: dict[str, StructuringCounts], ids: list[str]) -> np.ndarray:
    return np.array([[getattr(per[i], f) for f in _FIELDS] for i in ids], int)


def _delta_stat(ref_m: np.ndarray, cur_m: np.ndarray, metric: str):
    def stat(idx: np.ndarray) -> float | None:
        r = score_structuring(StructuringCounts(*(int(v) for v in ref_m[idx].sum(0))))
        c = score_structuring(StructuringCounts(*(int(v) for v in cur_m[idx].sum(0))))
        if r[metric] is None or c[metric] is None:
            return None
        return c[metric] - r[metric]
    return stat
```

Sign convention: **current minus reference**, so negative is degradation. State
it in the docstring; a sign error here inverts every alert.

Verdict from the interval: CI excluding 0 gives `DRIFT` with `direction` from
the sign of `delta`; CI containing 0 gives `NO_DRIFT`. `mde = (hi - lo) / 2`.

- [ ] **Step 4: Run, expect PASS**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_drift.py -v`

If the injected test does not flag at `fraction=0.25`, do **not** raise the
fraction to make it pass. Record the smallest fraction that does flag; that is
the sensitivity number Task 8 reports, and a detector that needs a 25% drop to
notice is a finding, not a bug to hide.

- [ ] **Step 5: Commit**

```bash
git add governance/drift.py tests/test_drift.py
git commit -m "feat(P3-3): paired BCa delta on the metric eval_runs publishes"
```

### Task 6: The two downgrades

**Files:**
- Modify: `governance/drift.py`
- Test: `tests/test_drift.py`

- [ ] **Step 1: Write the failing test, the one that encodes P3-2's finding**

```python
def test_the_two_real_windows_are_not_attributable():
    """Windows 7 and 25. Three independent reasons, all named.

    This is the honest answer on the only real pair this project has, and it is
    the module working, not failing.
    """
    ref = json.loads(COMMITTED_JUL.read_text(encoding="utf-8"))
    cur = json.loads(COMMITTED_AUG.read_text(encoding="utf-8"))
    r = compare_structuring_windows(ref, cur, replicates=2000)

    assert r.verdict is DriftVerdict.NOT_ATTRIBUTABLE
    assert r.comparability == ("max_tokens",)
    assert set(r.unmeasured_variance) == {"generation_sampling", "instrument"}
    assert r.delta is not None          # the statistic is still reported
    joined = " ".join(r.caveats)
    assert "6550" in joined and "6553" in joined   # the instrument moved
```

- [ ] **Step 2: Run it, expect FAIL**

- [ ] **Step 3: Implement both downgrades**

After the statistic: build `comparability` from
`config_from_artifact(ref).differing_fields(config_from_artifact(cur))`. Build
`unmeasured_variance` as empty when `controlled_pair=True`, else
`("generation_sampling", "instrument")`. If either tuple is non-empty, the
verdict becomes `NOT_ATTRIBUTABLE` whatever the interval said, and the delta,
CI and MDE stay in the result.

The instrument caveat names both `ref_facts` totals when they differ, because
"the decomposer moved" is not believable without the two numbers next to it.

`controlled_pair` is documented as: the current window is a deterministic
transformation of the reference artifact, so generation and instrument variance
are zero by construction. **Never true of two real runs.**

- [ ] **Step 4: Run the whole drift module, expect PASS. Commit**

```bash
git add governance/drift.py tests/test_drift.py
git commit -m "feat(P3-3): provenance and unmeasured-variance downgrades"
```

---

## Chunk 3: Confidence, sensitivity, and the CLI

### Task 7: The confidence adapter

**Files:**
- Modify: `governance/drift.py`
- Test: `tests/test_drift.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_confidence_collapse_is_flagged():
    rng = np.random.default_rng(0)
    ref = pd.DataFrame({"confidence": rng.normal(0.9, 0.02, 500)})
    cur = pd.DataFrame({"confidence": rng.normal(0.55, 0.05, 500)})
    r = compare_confidence(ref, cur)
    assert r.verdict is DriftVerdict.DRIFT
    assert any("self-reported" in c for c in r.caveats)


def test_an_unrecognized_evidently_payload_raises():
    """Fails loud, not closed.

    The pre-P3-3 stub walked metric["value"], found nothing under evidently
    0.5.1 (which nests under "result"), and returned False. A drift detector
    that reports "no drift" when it cannot read its own input is worse than no
    detector, because it is trusted.
    """
    with pytest.raises(ValueError, match="unrecognized"):
        _dataset_drift({"metrics": [{"metric": "DatasetDriftMetric",
                                     "value": {"dataset_drift": True}}]})


def test_mixed_models_in_a_confidence_window_are_not_comparable():
    ref = pd.DataFrame({"confidence": [0.9], "model": ["claude-sonnet-5"]})
    cur = pd.DataFrame({"confidence": [0.5],
                        "model": ["claude-sonnet-5", "claude-opus-4-8"][:1] * 1
                                 + ["claude-opus-4-8"]})
    assert (compare_confidence(ref, cur).verdict
            is DriftVerdict.NOT_COMPARABLE)
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

`_dataset_drift(payload)` reads `metric["result"]["dataset_drift"]` and raises
`ValueError("unrecognized evidently payload shape ...")` if no metric carries
it. Pinned to 0.5.1, verified: `DatasetDriftMetric.result` holds
`dataset_drift`, `drift_share`, `number_of_drifted_columns`.

`compare_confidence` checks that `model` and `model_effort`, where present, are
single-valued within each frame, returning `NOT_COMPARABLE` otherwise, then
runs `Report(metrics=[DataDriftPreset()])` on the `confidence` column only.
Every result carries the permanent caveat that confidence is unlabeled and
self-reported, so a move in it is not evidence of an accuracy change.

- [ ] **Step 4: Run, expect PASS. Commit**

```bash
git add governance/drift.py tests/test_drift.py
git commit -m "feat(P3-3): confidence drift via evidently, failing loud on shape change"
```

### Task 8: The sensitivity sweep

**Files:**
- Test: `tests/test_drift.py`

- [ ] **Step 1: Write the sweep**

```python
@pytest.mark.parametrize("fraction", [0.01, 0.02, 0.05, 0.10, 0.25])
def test_sensitivity_sweep(fraction, record_property):
    """The phase metric: drift detection sensitivity on a controlled drop.

    A property of THIS detector at n=120, not a claim about the system's
    stability. Recorded so the smallest flagged drop is evidence rather than a
    remembered number.
    """
    payload = json.loads(COMMITTED_AUG.read_text(encoding="utf-8"))
    r = compare_structuring_windows(
        payload, degrade(payload, fraction=fraction, seed=11),
        controlled_pair=True, replicates=2000)
    record_property("fraction", fraction)
    record_property("delta_f1", r.delta)
    record_property("verdict", r.verdict.value)
    assert r.verdict in (DriftVerdict.DRIFT, DriftVerdict.NO_DRIFT)
```

- [ ] **Step 2: Run and capture the output**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_drift.py -k sensitivity -v`

Record which fractions flag. That table is the evidence for the phase metric
and goes in the roadmap entry in Task 9.

- [ ] **Step 3: Commit**

```bash
git add tests/test_drift.py
git commit -m "test(P3-3): sensitivity sweep, the phase metric's evidence"
```

### Task 9: CLI, roadmap entry, and the gate

**Files:**
- Create: `scripts/run_drift_check.py`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Write the CLI**

`--reference` and `--current` take artifact paths, or `--reference-run` and
`--current-run` take `eval_runs` ids and resolve `provenance.artifact` through
`shared.db`. Print verdict, delta, CI, MDE, then every caveat on its own line.
Exit 0 for `NO_DRIFT`, 1 for `DRIFT`, 2 for `NOT_ATTRIBUTABLE` and
`NOT_COMPARABLE`, so CI can branch on it without parsing prose.

- [ ] **Step 2: Run it on the two real windows**

```bash
PYTHONPATH="$PWD" .venv/Scripts/python.exe scripts/run_drift_check.py \
  --reference governance/eval_artifacts/structuring_aci-bench-heldout-v1_20260714T032403Z.json \
  --current   governance/eval_artifacts/structuring_aci-bench-heldout-v1_20260831T205449Z.json
```

Expected: `NOT_ATTRIBUTABLE`, exit 2, three named caveats. Paste the real
output into the roadmap entry.

- [ ] **Step 3: Full suite and lint**

Run: `make test` then `make lint`
Expected: everything green, the old xfail **gone** rather than still xfailing.
The suite was 386 passed / 1 xfailed; the 1 xfail must now be a real pass, and
the count should rise by the new tests.

- [ ] **Step 4: Write the roadmap entry**

Under P3-3, following the P3-1 and P3-2 format: state the gate, show the
evidence (suite counts, the sensitivity table, the CLI output on the real
pair), and say plainly that `NOT_ATTRIBUTABLE` on windows 7 and 25 is the
module working. Record what would close each of the three reasons, and that
none of them are done here.

- [ ] **Step 5: Commit and open the PR**

```bash
git add scripts/run_drift_check.py docs/ROADMAP.md
git commit -m "feat(P3-3): drift check CLI, and the gate evidence"
git push -u origin p3-3-drift-detection
gh pr create --base main --title "P3-3: drift detection, and the verdict that refuses to be a boolean"
```

---

## Verification checklist

- [ ] P2-4's `d`, CI endpoints and acceleration are bit-identical to the committed artifact
- [ ] `tests/test_drift.py` has no `xfail`
- [ ] The injected drop flags; the smallest flagged fraction is recorded
- [ ] The real pair returns `NOT_ATTRIBUTABLE` naming all three reasons
- [ ] `DriftResult` has no boolean field
- [ ] An unrecognized Evidently payload raises
- [ ] `make test` and `make lint` clean, xfail count down to 0
