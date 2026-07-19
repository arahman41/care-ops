# P2-4: Coding Configuration Routing Benchmark

**Status:** design, revised after review round 2
**Roadmap task:** P2-4, as re-scoped in commit `f1cb3ab`
**Depends on:** P2-3 (merged, `7fdd02f`), P0-5 held-out split

---

## 1. What this measures, and what it does not

This benchmark decides what `shared/llm.py::ROUTING["coding"]` should hold.

**The unit under test is a `(model, effort)` configuration, not a model.** The
arms are Sonnet 5 at xhigh and Opus 4.8 at high, which differ in two factors
at once. No artifact may attribute a result to a model. "Configuration A beat
configuration B" is the only licensed phrasing. Resolving the factors would
need a 2x2, which quadruples cost and adds a multiple-comparison problem this
design does not have.

**This does not measure coding accuracy.** Neither held-out dataset carries
gold billing codes: `heldout_manifest.csv` is `dataset,encounter_id,split` and
ACI-Bench is `dataset,encounter_id,dialogue,note`.

Measured per arm:

| Metric | Definition | Denominator |
|---|---|---|
| Verified rate | `vocab.verified_rate(verified, not_found)` | checkable codes, deduplicated |
| Not-found rate | `1 - verified_rate`, and `None` when `verified_rate` is `None` | checkable codes, deduplicated |
| Pessimistic verified rate | `unchecked` counted as not verified | all codes, deduplicated |
| Unchecked share | codes classified `unchecked` | all codes, deduplicated |
| Codes per note | suggested codes after dedup | notes |
| Floor bounds | section 5 | checkable codes |
| Agreement | per-note Jaccard over normalized codes | notes where either arm emitted a code |
| Cost | tokens, and currency only via section 8's price table | per arm |
| Latency | wall-clock per call | per arm, p50 and p95 |

The verified rate says a code **exists in the pinned CMS release**, never that
it is **right for the note**.

### Degeneracies, stated rather than discovered

1. **It rewards timidity.** Two obvious diagnoses per note scores near 1.0;
   eight including specific ones scores lower. Coverage and specificity are
   what make a coding model useful and this metric penalizes both.
2. **`unchecked` is an unscored escape hatch that differs by arm.**
   `vocab.classify` rule 3 returns `unchecked` on a shape test against a
   vocabulary that is not vendored, so a fabricated five-digit number labeled
   CPT costs nothing while a fabricated ICD-shaped code is `not_found`.
3. **Duplicates.** `_enrich` does not deduplicate.

### Deduplication, and the two properties that must not be used

All counting deduplicates on **`normalize(code)` alone, within a note**, not
on `(system, normalize(code))`. Including `system` would contradict
`vocab.classify`, whose rules 1 and 2 deliberately ignore the declared system,
and would count the same code under two labels twice. Since label drift is
arm-specific and is exactly cause 3 of the floor, a system-keyed dedup would
partially reintroduce the degeneracy dedup exists to remove. Agreement uses
the same key, for the same reason.

**Dedup is per note, never global across the analysis set.** Global dedup
would collapse a common code appearing in 40 notes into one observation and
move the headline rate substantially, and it would break the note-level
bootstrap, which requires a note drawn twice to contribute twice.

Every `system` label seen for a code is retained alongside it, because cause 3
attribution needs them.

**Conflicting statuses resolve to `not_found`.** Deduplication can merge
occurrences that `classify` gave different statuses, and the resolution rule is
the single highest-leverage choice in the metric, so it is pinned rather than
left to the implementer.

A conflict is possible only between `not_found` and `unchecked`. `verified`
cannot conflict, because rules 1 and 2 ignore the label, so a code present in
either vocabulary verifies under every label it is given. The live case is a
code absent from both vocabularies, emitted once labeled `CPT` with CPT shape
(rule 3, `unchecked`) and once labeled `ICD-10` (`not_found`).

`not_found` wins. `unchecked` is an exemption from the denominator, and
letting a model earn that exemption by emitting the same code a second time
under a `CPT` label would reward precisely the label drift that degeneracy 2
and floor cause 3 exist to expose. Resolving the other way would remove the
code from scoring entirely, which is the more favorable treatment and the
wrong default.

> **`CodingOutput.verified_count` and `CodingOutput.not_found_count` are
> forbidden in this benchmark.** They count list entries un-deduplicated
> (`schemas.py:129,135`), and `vocab.verified_rate(verified, not_found)` takes
> exactly those two values in that order, so
> `verified_rate(out.verified_count, out.not_found_count)` is the
> lowest-friction wiring available and is wrong. Counts are recomputed from the
> deduplicated per-code verdicts.

Agreement is descriptive context only and never an input to the routing
decision.

---

## 2. The pre-registered decision rule

### The margin is stated on the not-found scale

An earlier draft set delta = 3.0 points on the verified rate. That is
mis-scaled: at a verified rate near 0.94 the estimand is really the roughly
6-point not-found rate, so a 3-point margin would declare two arms
"equivalent" at 4.5% and 7.5% not-found, one making two-thirds more
unverifiable-code errors than the other.

**delta = 1.5 percentage points on the not-found rate**, roughly a 25%
relative difference at the anticipated base rate. The difference tested is
`not_found_rate(A) - not_found_rate(B)`, which is the negation of the verified
rate difference and identical in magnitude.

### Scale convention, which is load-bearing

**Every member of the rate family (verified, not-found, unchecked share,
floor bounds), along with delta, `d`, and both interval endpoints, is
expressed in percentage points, on a 0 to 100 scale.** Section 4's `delta_b`
formula yields a proportion and is **multiplied by 100** before any comparison
in this section.

Two thresholds are deliberately **outside** this convention because they are
dimensionless ratios, and are written as such in their own formulas: volume
divergence (`0.25`) and intersection loss (`0.90`).

The derivation of the unchecked guard below divides by `v`. **That `v` is a
proportion in [0, 1], not points.** Writing `delta / v` with `v` in points
yields `1.5 / 94.0 = 0.016` points, a threshold that trips on essentially any
run and forces Inconclusive every time. This is the one place the convention
must be broken and it is marked rather than left to be discovered.

This is stated because omitting it degenerates the rule completely. A CI of
about `±0.03` on the proportion scale, compared against `(-1.5, +1.5)` read as
proportions, lies inside the margin always, so **every run would return
Equivalent** regardless of the data. Since this section insists the literal
wording is the rule, the scale has to be part of the wording.

### Reachability is checked before the run, not assumed

Equivalence requires the CI to fit inside `(-delta, +delta)`, which requires
`SE_diff < delta / 1.96`. Since `SE_diff = SE_arm * sqrt(2(1 - rho))`, the
equivalence branch is attainable only above a threshold `rho`. Earlier drafts
declined to estimate `rho` while labeling equivalence "a positive finding",
which asserted reachability rather than showing it.

**The pilot (section 8) estimates `rho` and the design effect and reports
whether the equivalence branch is attainable at the achieved n.** If it is
not, that is recorded before the full run, and the run proceeds knowing it can
return only Difference or Inconclusive. This check is itself a deliverable.

### The branches

Let `CI` be the 95% BCa interval on the paired difference in not-found rate
(section 4), and `d` the point estimate.

| Condition | Outcome |
|---|---|
| Any guard below is tripped | **Inconclusive**, naming the guard |
| `CI` lies entirely within `(-delta, +delta)` | **Equivalent.** Route on cost. |
| `CI` excludes zero and `abs(d) > delta` | **Difference.** Route on the lower not-found rate. |
| Otherwise | **Inconclusive.** Report, route on cost, record that the quality comparison did not resolve. |

`abs(d)`, not `d`. An earlier draft wrote "the point estimate exceeds delta",
which sends a clear five-point win for arm B (`d = -5`) to Inconclusive. In a
pre-registered rule the literal wording is the rule.

The Inconclusive branch exists because without it an underpowered study and a
genuine null produce the same action, which turns absence of evidence into
evidence of absence and makes a noisier experiment more reliably return "route
on cost".

### Guards, each mapping to an outcome

Evaluated first. Any trip forces **Inconclusive** with the guard named, and no
quality winner is declared.

| Guard | Threshold | Why |
|---|---|---|
| Unchecked divergence | `abs(unchecked_share(A) - unchecked_share(B)) > 1.6 pts` | The verified rates are computed over structurally different subsets. Calibrated so a tripped guard corresponds to about delta of movement in the pessimistic rate, per below. |
| Volume divergence | `abs(cpn_A - cpn_B) / ((cpn_A + cpn_B) / 2) > 0.25` | Reflects verbosity, not quality (degeneracy 1). Denominator is the **mean of the two arms**, so the guard is symmetric; relative to A and relative to B give different answers near the threshold. |
| Floor divergence | `max_possible_floor_gap > abs(d)` | The gap is a labeling or training-cutoff artifact, not coding quality (section 5) |
| Intersection loss | analysis set below 90% of 120 notes | Section 8 |

**Unchecked threshold derivation.** Since
`pessimistic = standard * (1 - unchecked_share)`, a share gap `g` moves the
pessimistic rate by about `v * g` where `v` is the verified rate. Setting
`v * g = delta` at `v` near 0.94 gives `g = 1.5 / 0.94`, about **1.6 points**.
An earlier draft used 3.0 points and claimed it was calibrated this way; it is
not, it permits a pessimistic-rate gap of nearly twice delta, and it errs in
the direction that lets a structural artifact be reported as a quality
difference.

**Floor divergence is a band against a scalar.** Floors are reported per arm as
bounds (section 5), so `abs(floor(A) - floor(B))` is not well defined. The
guard uses the **maximum possible gap** consistent with both bands:

```
max_possible_floor_gap = max( upper(A) - lower(B), upper(B) - lower(A), 0 )
```

**The operands are defined here, since a guard whose bounds are undefined
fires on an unknowable fraction of runs:**

- `lower(X)` = auto-classified causes 3 and 4 for arm X, as a percentage of
  that arm's checkable codes. The floor is at least this large.
- `upper(X)` = arm X's full not-found rate. The floor cannot exceed it, since
  every not-found code is one of the four causes.

Human adjudication, if performed, narrows the band by moving cause 2 out of
the residual; it never widens it.

This is the conservative choice, firing more readily, which is the correct
direction for a guard whose purpose is to prevent over-claiming.

**The pilot reports every guard's statistic**, not just `rho`. Section 2
checks that the Equivalent branch is reachable but nothing checks the guards
can be jointly untripped, and with both the unchecked and floor guards
plausibly firing, "Inconclusive, guard tripped" could be the most likely
outcome of the entire benchmark. That would be worth knowing before the spend
rather than after.

If the standard and pessimistic framings disagree on which arm is better, that
disagreement is reported as the finding regardless of branch.

### Terminal state when there is no price table

The Equivalent and Inconclusive branches both route on cost, and section 8
makes `governance/pricing.json` a required input that does not exist in the
repo today.

**If the price table is absent, that is a terminal state: `ROUTING["coding"]`
is left unchanged, the reason is recorded in the artifact, and no winner is
named.** An earlier draft closed this section with "whatever the outcome,
`shared/llm.py` records the winning configuration", which under a missing
price table obliges an engineer to name a winner anyway, and the nearest
available signal is raw token counts. That is exactly the inversion section 8
exists to prevent, since the xhigh arm emits more reasoning tokens while being
cheaper per token.

When a winner **is** named, `shared/llm.py` records the winning
**configuration** plus a one-line note.

---

## 3. Input construction

Each `SoapNote` is built from the ACI-Bench clinician reference note via
`aci_sections.bucket_sections`, concatenating the bodies of all sections
sharing a `primary` bucket, **joined with `"\n\n"`**. Both arms receive
byte-identical input, so any difference is attributable to the configuration
rather than to structuring variance. The P1-2 path is not run.

### The untested assumption is rank invariance

The number is **not** an upper bound on production behavior. The verified rate
is not monotone in input quality: on degraded input a model plausibly becomes
more conservative and emits fewer, more generic codes, which would **raise**
it. The direction is unknown and is not claimed.

The design assumes **rank invariance**, that whichever configuration wins on
clean clinician prose also wins on production-shaped input. This is untested.
A 15-note paired subset through the real P1-2 path is run and **reported
descriptively with no decision consequence**. At roughly 18 not-found events
per 120 notes, 15 notes yields about 2 events per arm, so a sign computed on
it is near a coin flip. An earlier draft gave this check a hard veto, which
would have vetoed at random. It is context, not a gate.

### Stratify on empty plan, not on fused

An earlier draft stratified on the fused `ASSESSMENT AND PLAN` header and
asserted 51 of 120 notes leave `plan` empty. **Measured, that is wrong:**

```
fused=False  plan_empty=False  ->  69
fused=True   plan_empty=False  ->  24
fused=True   plan_empty=True   ->  27
```

51 notes are fused but only **27** end with an empty `plan`; the other 24 also
carry a separate `PLAN`, `INSTRUCTIONS`, or `ORDERS` header that maps to the
`plan` bucket independently. Stratifying on fused would pool 24 populated-plan
notes with 27 empty-plan notes inside one stratum, which is the confound the
stratification exists to expose.

**The stratification variable is `plan == ""`, giving 27 and 93.** The
mechanism that motivates it, that an empty `plan` is itself a signal and that
`plan` drives procedure and supply codes which are disproportionately CPT
(`unchecked`) and HCPCS (checkable), applies to those 27. Metrics are reported
per stratum and pooled, following `structuring_eval.RunResult.strict_metrics`.

---

## 4. Sample, estimator, intervals

**All 120 ACI-Bench held-out encounters.** PriMock57's 7 are excluded on power
grounds. This is a decision, not a constraint: their free-text notes could be
handled, though not by the mechanism an earlier draft cited.
`structuring_eval.py:292` is `decompose_freetext(example.reference_note,
cache)`, which decomposes a reference note for scoring and marks every bucket
acceptable. It does not pass a PriMock note to a model as a `subjective` blob,
so it is not precedent for feeding one to the coding agent.

### Estimator

The point estimate is a **pooled ratio of sums**, matching
`vocab.verified_rate`'s docstring, which rejects averaging per-note rates
because that weights a one-code note the same as a twelve-code note and is
undefined where it matters most.

### Interval

Codes cluster within notes, so binomial intervals over codes are wrong.
Intervals come from a **note-level bootstrap with shared indices**:

1. Draw note indices with replacement, size `n = |analysis set|` (section 8),
   **not a hardcoded 120**.
2. **Both arms use the same resampled ids within a replicate.**
3. Compute
   `delta_b = (nf_A / checkable_A) - (nf_B / checkable_B)` over the resample,
   a difference of ratios-of-sums matching the point estimate.
4. 10,000 replicates. BCa intervals.

Step 2's failure mode is directional: resampling the arms independently
discards the positive between-arm correlation from shared note difficulty,
widening the interval, making it likelier to contain zero, and so likelier to
return the branch this spec already predicts. **Test: with two identical arms,
every replicate `delta_b` must be exactly 0.** This asserts the resampling,
which is the thing shared indices guarantee, rather than asserting a property
of the interval method downstream of it.

### BCa tie handling

With identical arms every `delta_b` is 0, so the naive bias correction
`z0 = Phi^-1(0)` is negative infinity and the acceleration is 0/0. A correct
BCa returns NaN or raises on that input, and the predictable response is a
silent fallback to percentile, changing the estimator on the headline
interval. The real run also has a large atom at exactly zero, from notes where
both arms emit identical code sets, and BCa reads an atom as bias.

**The tie convention is pinned:**

```
z0 = Phi^-1( ( #{delta_b < d} + 0.5 * #{delta_b == d} ) / B )
```

**The acceleration is pinned too**, because naming `0/0` as a defect and then
fixing only `z0` leaves the diagnosed failure open. Leave-one-note-out
jackknife over the analysis set, matching the note-level resampling:

```
a = sum(u_i^3) / ( 6 * ( sum(u_i^2) )^1.5 )
```

where `u_i` is the mean of the jackknife replicates minus the `i`-th
replicate. **When the denominator is zero, `a` is set to 0 and the artifact
records that it happened.** Silently setting `a = 0` without recording it
downgrades BCa to bias-corrected percentile with nothing failing, which is the
same class of silent estimator swap this section exists to prevent.

**BCa is hand-rolled, not delegated.** `scipy.stats.bootstrap(method="BCa")`
computes `z0` with the naive strict-`<` count, so it does not implement the
convention above. An engineer who hand-rolls `z0` and delegates `a` to scipy
produces a hybrid neither this section nor scipy's documentation describes.

Replicates drawing a zero denominator get `None` from `verified_rate`, never
`0.0`. **Such replicates are dropped, and `B` in the `z0` denominator becomes
the retained count**, not the requested 10,000, or `z0` is computed against a
denominator that includes replicates absent from its numerator. The dropped
count is recorded.

Seed and replicate count are recorded; without them the interval is not
reproducible.

### Power

Earlier drafts called `1.96 * SE_diff` the "minimum detectable difference".
That is the critical value, at 50% power; an 80%-power MDD is about
`2.8 * SE_diff`. Those naive figures are dropped entirely rather than
corrected, because clustering widens the interval while the paired design
narrows it whenever the arms correlate across notes, and **the net direction
is not determined a priori**. The design effect and `rho` are measured in the
pilot and reported with the interval actually obtained.

The illustrative figures used above (verified rate about 0.94, about 18
not-found events per arm, about 2.5 checkable codes per note) come from P2-3's
two-note live run and are **projections, not measurements**. The pilot
replaces them.

**delta and the unchecked guard are pre-registered constants and are never
recomputed.** delta = 1.5 points, unchecked guard = 1.6 points, both fixed at
the values section 2 derives. An earlier draft made them functions of the
pilot's measured `v`, which was wrong twice over:

1. **Noise amplification.** At 5 pilot notes and roughly 2.5 checkable codes
   per note, about 13 codes per arm, the margin would be quantized in 1 to 2
   point steps by single code verdicts. The chance the pilot sees zero
   not-found codes is about `0.94^25 = 0.21` pooled across arms, and
   **delta = 0 deletes the Equivalent branch entirely** and zeroes the
   unchecked guard so every run trips it. A one-in-five chance of destroying
   the decision rule before the run starts.
2. **It contradicted section 3.** That section rejects the 15-note
   rank-invariance check as near a coin flip at about 2 events per arm. The
   same objection applies with more force to a 5-note pilot, except there the
   quantity is descriptive and here it is load-bearing.

Recomputing a pre-registered margin from data, even train data, also weakens
the pre-registration that is the point of section 2.

**The pilot reports `v` as a check, not as an input.** If the pilot's `v`
differs from the 0.94 projection by more than 5 points, that is surfaced to a
human before the full run, as information about whether delta is sized
sensibly. It does not automatically change any threshold.

---

## 5. The floor

Four causes from P2-3 spec 1a. **Two are decidable, one is conditionally
decidable, one is not.**

| Cause | Decidable | How |
|---|---|---|
| 4. Degenerate input | yes | fails `_CODE_SHAPE_RE` or is in `_PLACEHOLDERS` |
| 3. Shape-compatible with CPT, membership unverifiable | upper bound only | `_looks_like_cpt(key)` and declared system is not `CPT` |
| 2. Real but absent from the pinned release | only if section 5a clears | prior-release membership |
| 1. Fabricated | no | residual |

### 5a. Cause 2 vendoring is conditional on a measurement

An earlier draft argued that models trained before FY2026 emit FY2024 and
FY2025 codes, therefore cause 2 is "plausibly the largest floor component",
and added vendoring FY2025 as scope. **That is a non-sequitur.** An old code
that still exists in FY2026 verifies fine and contributes nothing to the
floor. The only codes a prior pin can rescue are those in FY2025 **and deleted
from** FY2026, and ICD-10-CM churn is overwhelmingly additive.

**The FY2025 vendoring is approved only if `|FY2025 \ FY2026|` is large enough
to matter.** That set size is knowable from one diff and must be measured and
recorded first. If it is in the tens against roughly 18 not-found events per
arm, the vendoring cannot move the attribution and is dropped, leaving cause 2
inside the residual with cause 1 and the floor reported as the wider band. In
that case `vocab_floor_version` is recorded as the string `"none"`, not left
absent, so an artifact always states which floor pin produced its attribution.

Note also that one prior pin decides FY2025 only. A code retired before FY2025
still misattributes to cause 1, so even the cleared case is a partial fix and
the table says so.

If it clears, the vendoring reproduces what made FY2026 safe rather than
gesturing at it. From `data/vocab/PROVENANCE.md`, all three must be specified:

- **The dated release**, since FY2025 has an original and a mid-year update
  and the choice changes the attribution. "FY2025" alone is not a pin.
- **The `icd10cm_order_*.txt` sibling trap.** The same zip ships an order file
  whose lines lead with a sequence number, so a first-token parse loads
  sequence numbers as codes and the sha256 pin covers whatever was downloaded
  and notices nothing.
- **The member path inside the zip**, which is year- and layout-specific.

**`VOCAB_VERSION` does not change.** It names the pins `classify` uses, and an
engineer adding a third file will reasonably bump it, which would trip section
6's replay hard-error on every prior artifact and rewrite
`vocabulary_version` on every production `CodingOutput` for a file production
never reads. The floor pin lives only in `vocab_floor_version`.

### 5b. Cause 3 is an upper bound

`_looks_like_cpt` (`vocab.py:91`) is a shape regex over `_CPT_RE`
(`vocab.py:48`) and CPT is not vendored, so
a matching code cannot be known to be real; a fabricated five-digit number
labeled "ICD-10" satisfies it identically. Classifying such codes as cause 3
rather than cause 1 deflates the fabrication estimate in the flattering
direction, so this is reported as an **upper bound on cause 3**, never as a
count of real mislabeled CPT codes.

**For a deduplicated code carrying several labels**, which section 1's
conflict rule guarantees is a non-empty population, the predicate is satisfied
if the shape matches and **any** retained label is not `CPT`. This is the
inclusive reading, consistent with the quantity already being an upper bound.

### 5c. Cause 4 needs predicates that do not exist

`shared/vocab.py` has `normalize` and `_CPT_RE` and no general shape test. The
benchmark defines locally, and documents as benchmark-side heuristics:

```python
_CODE_SHAPE_RE = re.compile(r"^[A-Z0-9]{3,7}$")
_PLACEHOLDERS = frozenset({"NONE", "UNKNOWN", "TBD", "NA", "NIL", "PENDING"})
```

`_PLACEHOLDERS` is required because `normalize` uppercases and strips **the
decimal point only** (`vocab.py:60`), not punctuation generally. So `NONE`,
`UNKNOWN`, and `TBD` survive as bare alphanumerics, **pass** a shape test, and
would land in the residual as cause 1, inflating the fabrication estimate.
Forms retaining punctuation, such as `N/A`, are caught by shape alone.

### 5d. The floor is arm-specific

It is not a shared offset that cancels in the paired difference. Cause 3 is a
labeling behavior and cause 2 a function of training cutoff, both arm-specific.
Floors are reported per arm and the floor-divergence guard in section 2 fires
when the floor gap can explain the verified-rate gap.

### 5e. The roster is not committed

Columns: encounter id, arm, system labels seen, code, `model_description`,
auto-classified cause, blank adjudication column. Named `model_description`
because the vendored files are codes only, so the only description available
is the model's own prose about its own `not_found` code, which is an
adjudication-biasing input.

**The roster carries per-encounter diagnosis codes and model prose, so it is
written to a gitignored `.full.json` sibling and is not committed**, following
`structuring_eval.py:9-14`, under which committed artifacts carry no clinical
text.

---

## 6. Storage, artifact, replay

### Schema

```sql
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS metrics JSONB;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS model_effort TEXT;
```

Idempotent, appended to `db/schema.sql`. `model_effort` is added because
effort is half of what distinguishes the arms, and `eval_runs` records model
without it. (`model_inventory` also lacks an effort column; that is out of
scope here.)

A coding row leaves `accuracy`, `f1`, `precision`, `recall` NULL. Writing a
verified rate into `accuracy` would mislabel it exactly where P3-1, P3-2, and
P3-5 read, propagating into the dashboard automatically.
`evaluate.record_structuring_run` is precedent for a NULL `accuracy` only: it
passes `metrics["f1"]`, `["precision"]`, and `["recall"]` unconditionally and
would `KeyError` on a coding payload, so a separate `record_coding_run` writer
is required.

**Phase 3 consumers must be verified to tolerate an all-NULL accuracy family**
before this lands, or the break is merely deferred.

### Artifact

Per arm: everything in section 1's table, plus `n_notes`, `n_codes_deduped`,
`n_checkable`, `n_verified`, `n_not_found`, `n_unchecked`, `n_failed`,
`failure_reasons`, per-stratum blocks keyed on `plan_empty`, per-note token
counts and latencies, `floor_cause_counts`, `floor_adjudicated`,
`requested_model`, `requested_effort`, `observed_model`, `prompt_version`,
`max_tokens`.

**Plus a top-level comparison block, which an earlier draft omitted entirely:**

```
n_analysis, delta_point, delta_ci95, delta_margin, branch_fired,
guards_tripped, rho_observed, design_effect, bootstrap_seed,
bootstrap_replicates, bca_acceleration, bca_acceleration_degenerate,
equivalence_attainable, latency_notes_contributing
```

`n_analysis` is the intersection size, without which the intersection-loss
guard cannot be audited and `n_notes` is ambiguous between "notes this arm
parsed" and "the analysis set". `delta_margin` is the value of delta actually
applied, after the pilot recomputation described in section 4.

Everything in this block plus each arm's section 1 metrics is what lands in
`eval_runs.metrics`; the per-note token, latency, and verdict detail stays in
the artifact only.

Section 2 operates on the CI of the **difference**, so without this the
artifact cannot be audited against the pre-registered rule, and a reader
holding two per-arm CIs can only check them for overlap, which is the classic
error and is biased toward "no difference".

Run-level: `split_digest`, `vocab_version`, `vocab_floor_version`,
`price_table_ref`.

`observed_model` is the id echoed back by the API on `resp.model`, not the
requested one. **This is not, as an earlier draft claimed, what
`structuring_eval.py:246` does:** that value originates at
`services/intake/structure.py:66`, which returns `model` from
`ROUTING["structuring"]`, so it carries the **requested** id and the comment
at `structuring_eval.py:243` overclaims for the same reason. P2-4 is
establishing this practice, not following it.

**Matching is by resolved family, not string equality.** `ROUTING` holds bare
aliases such as `claude-sonnet-5` while the API echoes a resolved snapshot id,
so exact equality would hard-fail on the first note. The check is that the
echoed id starts with the requested alias, and any mismatch under that rule
fails the run.

`split_digest` is carried because an artifact that does not name its split is
unmoored.

**Row shape: one `eval_runs` row per arm**, each carrying its own `model`,
`model_effort`, and per-arm metrics, with the comparison block duplicated into
both rows' `metrics` so either row is self-describing. Phase 3 reads this
table and a single row cannot hold two models.

### Replay

A verdicts-only JSON artifact in `governance/eval_artifacts/`, plus a replay
path that recomputes every **rate** from stored per-code verdicts and errors
on disagreement.

**Cost and latency are measured-once, not recomputed.** `llm_cache.Cache.put`
stores a bare string, and wall-clock latency is not a property of a cached
response, so an earlier draft's claim that replay "recomputes every number"
was false for exactly the class of number that decides the Equivalent branch.
Per-note token counts and latencies are carried in the artifact, and replay
verifies the stored aggregates match the stored per-note values rather than
re-deriving them from responses.

**Replay hard-errors when stored `vocab_version` differs from current
`VOCAB_VERSION`.** The vocabulary is correctly absent from the response cache
key since it does not change what the model writes, but it does change the
verified rate, so a warm-cache re-run under a bumped pin would recompute a
different number from byte-identical responses and look perfectly plausible.

---

## 7. The routing seam

### The bug an earlier draft would have shipped

`shared/llm.py::call()` re-reads `ROUTING[component]` internally at line 115.
`agent.run()` reads `ROUTING["coding"]` at **line 105** and uses it only for
`log_decision`. Adding overrides to `run()` alone would change the logged
label and not the API call, so both arms would issue identical
Sonnet-5-at-xhigh requests while the artifact named one Opus, producing two
near-identical rates confirming this spec's own stated expectation. A
plausible result, agreeing with the prior, with nothing looking wrong.

### The seam

1. **`shared/llm.py` gains `call_detailed()`** returning a frozen dataclass:

   ```python
   @dataclass(frozen=True)
   class LLMResult:
       text: str
       model: str            # resp.model, what actually ran
       input_tokens: int
       output_tokens: int
       stop_reason: str      # never "max_tokens"; call_detailed raises first
   ```

   `stop_reason` cannot hold `"max_tokens"`, because `TruncatedResponseError`
   is raised before the result is constructed. The field is kept for the other
   values and the comment prevents a reader concluding truncation can be
   detected by inspecting it.

   `call()` becomes a thin wrapper returning `.text`, so **no existing caller
   changes**. This is required, not cosmetic: `call()` returns `str` and
   discards the response, so `resp.model` and `resp.usage` never escape it,
   and section 6 mandates `observed_model` and token counts. The alternatives
   are re-issuing `_client.messages.create` inside the benchmark, which
   defeats the "one place for routing" purpose of this seam, or changing
   `call()`'s return type and touching every caller.

2. **`call_detailed()` accepts `model` and `effort` overrides.** Because
   `ROUTING` stores `None` as a meaningful effort for `care_gap`,
   `transparency`, and `eval_judge`, a plain `effort: str | None = None`
   default collides "not overridden" with "explicitly no effort". A module
   sentinel `_UNSET` is used instead. Harmless for P2-4's two arms, but this
   is the one place model routing lives.

3. **`agent.py` extracts `parse_and_enrich(raw: str) -> CodingOutput`**
   holding the `extract_json`, non-dict, and `ValidationError` guards plus
   `_enrich`. `run()` calls it; the benchmark calls it. One parsing path,
   which is what P2-3's spec on `classify` exists to protect.

4. The benchmark calls `call_detailed()` with the arm's explicit
   `(model, effort)`, then `parse_and_enrich`. It does **not** call `run()`.

### Why the benchmark cannot call `run()`

- `AgentInput.encounter_id` is `int` (`schemas.py:33`); ACI ids are strings
  like `D2N068`.
- `run()` unconditionally calls `log_decision`, which INSERTs into
  `agent_decisions` with NOT NULL foreign keys to `encounters(id)` and
  `notes(id)`. 240 benchmark rows would land in the production audit table
  that P2-7 and Phase 3 read for drift.
- `run()` consults no cache.

### Tests

- Assert the **outbound request** carries the override: monkeypatch
  `_client.messages.create`, check `kwargs["model"]` and
  `kwargs["output_config"]["effort"]`. A test asserting only that the default
  comes from `ROUTING` passes under the bug and is worthless.
- Assert `observed_model` matches the requested arm per note; mismatch fails
  the run.
- Assert `call()`'s behavior and signature are unchanged for existing callers.
- Assert `_UNSET` and an explicit `effort=None` are distinguishable.

---

## 8. Price table, caching, failures, pilot

### The price table is a required input

Cost is meaningless in raw tokens here. Effort drives reasoning-token volume
(`agent.py:24-25`), so Sonnet-at-xhigh will plausibly emit **more** tokens
than Opus-at-high while being far cheaper per token. Summing tokens and
picking the lower would route to Opus as "cheaper", the inverted answer, in
the branch this design most expects to fire.

**`governance/pricing.json` is a committed input** carrying per-model input
and output prices, a source URL, and a retrieval date. It is supplied by the
user, since no current pricing is available in this environment and an
invented table would be the same class of error this re-scope exists to
prevent.

**If the price table is absent, the benchmark refuses to declare a cost
winner** and reports tokens and latency descriptively, leaving routing
deferred. That is a stated outcome, not a crash.

### Cache key

An earlier draft justified folding effort into `prompt_version` by claiming
the arms would otherwise collide. **That was wrong:**
`llm_cache.cache_key(task, model, prompt_version, payload)` folds in `model`
at line 25 and the arms differ in model. The fix is kept for the real reasons:

- **Within-arm drift.** If `_MAX_TOKENS` or `_SYSTEM` changes between pilot
  and run, or between run and re-run, an unmodified key produces silent
  **hits** blending two configurations into one number. This is what
  `agent.py:28-33` warns about.
- **Cross-task collision** on task plus model plus payload.

So `version = f"{effort}|{hash_prompt(_SYSTEM)}|max{_MAX_TOKENS}"`, per
`structuring_eval.py:82`.

### What the cache stores

**A serialized `LLMResult`, not the bare response text.** On a cache hit
`call_detailed()` never runs, so a bare-string cache loses `observed_model`,
`input_tokens`, and `output_tokens` for every completed note. 240 calls at
xhigh and high effort is a long run, so interruption and resume is a normal
event, and on resume the cost inputs to the Equivalent branch would silently
be missing for exactly the notes already paid for. The acceptance criterion
"`observed_model` recorded per note" would be tickable while false.

`structuring_eval.generate_soap` already caches `note.model_dump_json()`
rather than raw text, so this needs no change to `llm_cache.Cache`. Note that
`LLMResult` is a frozen dataclass, not a pydantic model, so it has no
`model_dump_json`; explicit `to_json`/`from_json` helpers are part of the
work. This fails loudly if forgotten.

**Latency is the one field that does not survive a hit.** It is wall-clock, not
a property of the response. Latency aggregates are therefore computed over
cache-miss notes only, and the artifact records how many notes contributed.

### Failure policy

`_MAX_TOKENS = 5000` was pinned from **Sonnet-at-xhigh** usage over two notes;
Opus at high has a different reasoning profile and may truncate, which
`call()` raises as `TruncatedResponseError`.

- Per-arm failure counts and reasons are reported, never silent drops.
- **The analysis set is the intersection** of notes both arms parsed, so the
  paired bootstrap sees the same notes. The bootstrap resamples
  `n = |intersection|`.
- A single failure does not abort the run, unlike
  `structuring_eval.evaluate_examples`.
- **The void threshold is on the intersection, not per arm.** Two arms each
  failing 8% on disjoint notes would drop 16% of the analysis set without
  tripping any per-arm threshold. The run voids if the intersection falls
  below 90% of 120 notes.
- **Attrition is non-random and the void threshold bounds only its size.** If
  Opus-at-high truncates, it will truncate on the longest notes, and those
  notes leave the analysis set for **both** arms, biasing the paired
  comparison toward notes where the tighter-budget arm was comfortable. The
  artifact records the reference-note length distribution of dropped notes
  against retained ones so the shape of the loss is visible rather than
  merely bounded.

### Pilot

**Drawn from the train split, never the held-out set.** `governance/heldout.py`
deliberately exposes no train path ("Nothing here ever returns a train or dev
encounter"), so the pilot needs a small separate loader rather than a change to
that module. This is not a held-out policy violation; it is the opposite of
one. The split has 67 ACI train encounters available, so 5 is comfortable.

Pilot notes would
otherwise be held-out notes whose responses are cached and carried into the
full run, so proceeding after seeing their outcomes would condition on partial
outcome data from inside the analysis set, an optional-stopping violation.

The pilot reports token usage, latency, truncation rate, codes-per-note, its
own `v`, every guard statistic from section 2, `rho`, and the design effect,
and answers whether the equivalence branch is attainable. Its `v` is a
sanity check on delta's sizing, never an input (section 4). It does **not**
report a held-out verified rate.

**The pilot draw is pinned**, by recorded encounter ids or a recorded seed, so
it cannot be redrawn until it gives a preferred answer.

The Batch API would halve cost and the guide endorses it for offline
re-scoring, but it is not used: it adds an asynchronous path to
`shared/llm.py` under a task whose purpose is a trustworthy number. Revisit if
the pilot shows cost is prohibitive.

---

## 9. Acceptance criteria

- [ ] `call_detailed()` added returning `LLMResult`; `call()` unchanged for
      existing callers; `_UNSET` sentinel distinguishes "not overridden" from
      "explicitly no effort"
- [ ] Test asserts the **outbound request** carries the model and effort
      overrides
- [ ] `parse_and_enrich` extracted and shared by `run()` and the benchmark
- [ ] `observed_model` recorded per note; mismatch fails the run
- [ ] `ALTER TABLE` for `metrics` and `model_effort`, idempotent
- [ ] `record_coding_run` added; `record_structuring_run` untouched
- [ ] Phase 3 consumers verified against an all-NULL accuracy family
- [ ] `prompt_version` folds in effort, prompt hash, `max_tokens`
- [ ] Dedup on `normalize(code)` alone, **per note**;
      `verified_count`/`not_found_count` unused; test asserts a doubled code
      counts once and that a `not_found`/`unchecked` conflict resolves to
      `not_found`
- [ ] All rates, thresholds, and interval endpoints in percentage points;
      test asserts a proportion-scale CI cannot satisfy the Equivalent branch
- [ ] Bootstrap uses shared indices and resamples `n = |intersection|`; test
      asserts identical arms give every replicate delta exactly 0
- [ ] BCa hand-rolled with the pinned tie convention and jackknife
      acceleration; degenerate acceleration recorded, not silently zeroed;
      `None` denominators handled; seed and replicate count recorded
- [ ] Cache stores a serialized `LLMResult`; test asserts `observed_model` and
      token counts survive a cache hit
- [ ] Missing price table is a terminal state leaving `ROUTING` unchanged
- [ ] `|FY2025 \ FY2026|` measured and recorded; FY2025 vendored only if it
      clears, and if so with dated release, order-file trap, and member path
      specified, and `VOCAB_VERSION` unchanged
- [ ] `_PLACEHOLDERS` catches `NONE`, `UNKNOWN`, `TBD`
- [ ] Floor reported per arm as bounds; cause 3 labeled an upper bound
- [ ] Roster written to a gitignored `.full.json`, not committed
- [ ] Stratification on `plan == ""`, reported as 27 and 93
- [ ] Rank-invariance check reported descriptively with no decision consequence
- [ ] `governance/pricing.json` present with source and date, or the run
      declines to name a cost winner
- [ ] Pilot from train split reports `rho`, design effect, and whether
      equivalence is attainable, before the full run
- [ ] Analysis set is the intersection; run voids below 90%
- [ ] Artifact carries the top-level comparison block including `delta_ci95`
      and `branch_fired`
- [ ] Replay recomputes every rate and hard-errors on `vocab_version` mismatch;
      cost and latency verified against stored per-note values
- [ ] Decision rule applied literally, including `abs(d)` and the Inconclusive
      branch; winning **configuration** recorded in `shared/llm.py` **when one
      is named**, and `ROUTING` left unchanged when the price table is absent
- [ ] No artifact describes a number as coding accuracy or attributes a result
      to a model rather than a configuration
