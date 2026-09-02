# P3-3: Drift Detection

**Status:** design, awaiting review
**Roadmap task:** P3-3
**Depends on:** P3-1 (`GenerationConfig`, window semantics, guarded writer),
P3-2 (window 2, and the three reasons its delta is not drift)
**Model/effort:** Opus at xhigh, per `docs/MODEL-EFFORT-GUIDE.md` line 75. This
task consumes the headline numbers rather than computing one, so it is not a
max-effort task. See section 10.

---

## 1. The gate

> **P3-3 Drift detection.** Done when `governance/drift.py` compares a reference
> window against a current window and, given an injected accuracy or confidence
> drop in a controlled test, flags it. The test in `tests/test_drift.py` passes.

Two signals, not one. They have different reach and the difference matters:

| Signal | Source | Agents | Labeled |
|---|---|---|---|
| accuracy | eval artifacts, per-encounter fact verdicts | `note_structuring` only | yes |
| confidence | `agent_decisions.confidence` | all four | no, self-reported |

Both are in scope. Confidence is the only cross-agent signal this system has,
and it is also the weaker one, because a model's self-reported confidence can
move without its accuracy moving at all. The design carries that asymmetry into
the output rather than flattening it.

## 2. The hollow version of this gate, and why it is refused

`governance/drift.py` today feeds Evidently a synthetic normal `confidence`
column and walks `metric["value"]` looking for `dataset_drift`. Evidently 0.5.1
nests that under `metric["result"]`, verified against the pinned install:

```
metric: DatasetDriftMetric | result keys: ['drift_share', 'number_of_columns',
        'number_of_drifted_columns', 'share_of_drifted_columns', 'dataset_drift']
```

Changing one dictionary key makes `tests/test_drift.py` pass and closes the
gate today. It would flag a shift between two `numpy` normal distributions in a
column no agent in this repo ever emits, and would tell us nothing about
whether the system drifted. The gate would be met and the metric it unlocks,
"drift detection sensitivity on a controlled injected drop", would be a
statement about `numpy`.

The one-key fix is still made. It is necessary and not sufficient.

## 3. What the real data can and cannot support

Two windows exist. Verified from the committed artifacts, free:

```
keysets identical: True    (120 encounter ids, same order)
split_digest A == B: True
cfg A  claude-sonnet-5 high b7b42093e9a7 max_tokens=None
cfg B  claude-sonnet-5 high b7b42093e9a7 max_tokens=8000

counts A  ref_facts 6550  captured 5850  placed 5146  gen 7425  supported 7210
counts B  ref_facts 6553  captured 5875  placed 5196  gen 7588  supported 7383
```

Three consequences, and the design is shaped by all three.

**The data is paired.** Both windows score the same 120 encounters. Evidently's
`DataDriftPreset` runs two-sample tests that treat the windows as independent
and discard the pairing, which is where its power goes on an n of 120 against a
delta of +0.005 f1. Accuracy therefore uses a paired test. Evidently keeps the
confidence stream, where decisions across two time ranges genuinely are two
independent samples. Each tool is used where it is valid.

**The pair is not certified comparable.** `differing_fields` returns
`('max_tokens',)`, because July's cap was never recorded and window 2's is
8000. P3-1 built that signal precisely so this would be visible in data.

**Two variance components are unmeasured.** The encounter bootstrap in section
5 measures encounter-sampling uncertainty only. Effort-driven calls sample, so
two runs of an identical configuration differ with no vendor change, and no
same-day repeat run exists to measure that. Separately, the decomposer and
judge are themselves model calls: byte-identical reference notes produced 6,553
reference facts in August against 6,550 in July, so the instrument moved too.
Neither component is inside the CI, and a verdict that ignores them would be
overconfident about the one comparison we actually have.

## 4. Modules

**`governance/bootstrap.py`** (new, pure numeric). The resampling and BCa
engine lifted out of `coding_bootstrap.py` and made statistic-agnostic: the
caller passes `stat(idx) -> float | None`, the engine owns shared-index
resampling, dropping zero-denominator replicates, the jackknife acceleration,
and the mid-rank `z0` convention that scipy does not implement.

**`governance/coding_bootstrap.py`** keeps `NotePair`, `ratio_diff`,
`BootResult` and its public entry point, and delegates the engine. Algorithm,
seed handling and index draw order are unchanged. It protects P2-4's published
routing decision, so section 8 pins its exact output.

**`governance/structuring_eval.py`**. `replay()` already builds per-encounter
`StructuringCounts` in its loop and sums them. That loop becomes
`per_encounter_counts(payload) -> dict[str, StructuringCounts]`, and `replay`
sums its values. Drift then reads its observations from the same per-fact
verdicts the headline is recomputed and cross-checked from. There is no second
parser of the artifact format that could disagree with the first.

**`governance/drift.py`** (rewritten; no DB, no network). `DriftVerdict`,
`DriftResult`, `compare_structuring_windows`, `compare_confidence`. No boolean
field appears anywhere in the return type.

**`scripts/run_drift_check.py`**. Takes two `eval_runs` ids or two artifact
paths, prints verdict, statistic and caveats. The database is an index only,
supplying `provenance.artifact` and `provenance.generation`, so the whole
statistical core is testable in CI with no Postgres, following the
module-local `skipif` idiom in `tests/test_registry.py`.

Import edges run one way: `drift -> bootstrap`, `drift -> structuring_eval`,
`drift -> eval_runner` (for `GenerationConfig`), never back.

## 5. The statistic

For each of the 120 encounters, both windows contribute a `StructuringCounts`.
Each bootstrap replicate draws one index vector and applies it to **both**
windows, sums each window's counts under it, scores both through the existing
`score_structuring`, and takes the difference. The estimand is therefore the
difference in the same micro-averaged, ratio-of-sums quantity that `eval_runs`
publishes, not the mean of per-encounter f1s, which is a different number that
appears nowhere in this project.

Drift adds no metric arithmetic of its own. `evaluate.py` stays the only place
it lives, which is the rule that keeps the headline auditable from one place.

`DriftResult` carries: verdict, metric name, both point values, delta, BCa CI,
direction, seed, replicates requested, retained and dropped, `n_paired`,
`comparability` (the `differing_fields` tuple), `unmeasured_variance`, and
`caveats`.

## 6. Verdict rules, in order

1. **Structural refusal, nothing computed.** Different `dataset_ref`, different
   `split_digest`, different agent, or non-identical `encounter_id` keysets
   gives `NOT_COMPARABLE`. Such pairs are not two measurements of one thing,
   and quietly inner-joining them would invent a comparison.
2. **The statistic.** Paired BCa delta and CI on the requested metric.
3. **Significance and direction are separate.** `DRIFT` means the metric moved
   beyond sampling noise; `direction` is `degradation` or `improvement`. A
   significant improvement still means the hosted model moved under a
   configuration held fixed, which is what the P3-1 window definition exists to
   expose. P4-1 alerts filter on degradation; the detector does not decide that
   for it.
4. **`NO_DRIFT` never means "no change".** It means no change larger than this
   design could detect, so the result always carries the CI half-width as a
   minimum detectable effect. On n=120 that will be large next to the observed
   +0.005 f1, and saying so is the point.
5. **Provenance downgrade.** A non-empty `differing_fields` gives
   `NOT_ATTRIBUTABLE` whatever the statistic says. The delta and CI stay in the
   result; they simply cannot be assigned to the vendor's model.
6. **Unmeasured-variance downgrade.** `unmeasured_variance` names any of
   `generation_sampling` and `instrument` that apply, and a non-empty tuple
   downgrades to `NOT_ATTRIBUTABLE`. Where the current window is a
   deterministic edit of the reference artifact, as in the controlled test,
   both are zero by construction and the tuple is empty, so an injected drop
   still flags.

**Consequence, stated so no reader is surprised by it:** on the real pair, rows
7 and 25, this returns `NOT_ATTRIBUTABLE` for three independently named
reasons. That is P3-2's finding turned into enforced behavior instead of prose
in the roadmap.

There is no `drift_detected: bool`. A consumer cannot read past the caveats to
a boolean, because there is no boolean to read. This is the same shape as
P3-1's refusal to let an unscoreable agent write into the accuracy family.

## 7. The confidence adapter

A confidence window is `(agent_name, model, model_effort)` held fixed over a
time range, the same comparability contract `GenerationConfig` enforces for
accuracy. `agent_decisions` stores all three per row, so mixed models inside a
range gives `NOT_COMPARABLE` rather than a comparison of two different systems.

Evidently supplies the two-sample test, read from
`metric["result"]["dataset_drift"]`, with per-column detail from
`drift_by_columns`. Every confidence verdict carries a permanent caveat: the
signal is unlabeled and self-reported, so a move in it is not evidence of an
accuracy change.

**Unknown payload shape raises.** The current stub walks the payload
defensively and returns `False` when it recognizes nothing, so an Evidently
upgrade silently becomes "no drift detected". A detector that fails closed is
worse than no detector, because it is trusted. The shape assertion is pinned to
0.5.1 so an upgrade fails loudly in CI.

## 8. Tests and evidence

All free, no API calls, no database, no `data/`.

| Test | Asserts |
|---|---|
| injected degradation | a seeded degradation of the committed window-2 artifact returns `DRIFT`, `direction=degradation` |
| null comparison | an artifact against itself gives delta exactly 0 and `NO_DRIFT` |
| the real pair | both committed artifacts give `NOT_ATTRIBUTABLE`, naming `max_tokens`, `generation_sampling` and `instrument` |
| structural refusal | mismatched keysets give `NOT_COMPARABLE` with no statistic computed |
| no boolean escape hatch | `DriftResult` exposes no boolean drift field |
| Evidently shape | an unrecognized payload raises rather than returning no-drift |
| sensitivity sweep | injected drops across a grid at a fixed seed, recording the smallest reliably flagged |
| P2-4 regression | the extracted engine reproduces P2-4's exact `d` and CI endpoints from the committed coding artifact |

The sensitivity sweep is the evidence for the phase metric, "drift detection
sensitivity on a controlled injected drop". It is a property of this detector
at n=120, not a claim about the system's stability.

## 9. Out of scope, deliberately

- **No `drift_alerts` table, no persistence.** P3-5 computes on demand. An
  unused table is a schema commitment made before there is a reader.
- **No PriMock57.** n=7, and its accuracy is NULL by construction.
- **No paid run.** The generation-sampling baseline stays P3-2's recorded debt,
  which is exactly why rule 6 exists rather than being papered over.
- **No dashboard work.** P4-1 consumes this.

## 10. Risks

**The `coding_bootstrap` extraction touches a file that protects a published
number.** P2-4's arm A versus arm B routing decision rests on it. Mitigated by
the regression test in section 8, which pins `d` and both CI endpoints from the
committed artifact. If the extraction changes them at all, CI fails.

**`NOT_ATTRIBUTABLE` on the only real pair may read as the module not
working.** It is the module working. The roadmap entry and the CLI output both
have to say so in words, or a later reader will "fix" it.

**A third window will not automatically be attributable.** Closing
`max_tokens` requires a window whose config matches window 2, not window 1.
Closing `generation_sampling` requires a same-day repeat run. Both are P3-4 or
later decisions, and this spec does not pre-commit them.
