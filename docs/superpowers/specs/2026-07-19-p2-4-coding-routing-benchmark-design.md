# P2-4: Coding Configuration Routing Benchmark

**Status:** design, revised after review round 1
**Roadmap task:** P2-4, as re-scoped in commit `f1cb3ab`
**Depends on:** P2-3 (merged, `7fdd02f`), P0-5 held-out split

---

## 1. What this measures, and what it does not

This benchmark decides what `shared/llm.py::ROUTING["coding"]` should hold.

**The unit under test is a `(model, effort)` configuration, not a model.**
The two arms are Sonnet 5 at xhigh and Opus 4.8 at high, which differ in two
factors at once. This design cannot attribute a difference to either factor
alone. Every artifact must therefore say "configuration A beat configuration
B" and never "Opus beat Sonnet." No model-level claim is licensed here. A
model-level claim would require holding effort fixed across arms, and a 2x2
resolving both factors would quadruple cost and add a multiple-comparison
problem this design does not have.

The confound also lands on cost, which is the branch most likely to decide
this. Effort level drives reasoning-token volume directly (`agent.py:24-25`),
so comparing Sonnet-at-xhigh tokens against Opus-at-high tokens conflates
per-token price with reasoning budget. "The cheaper arm" means the cheaper
configuration, nothing more.

**This does not measure coding accuracy, and no artifact may describe it that
way.** Neither held-out dataset carries gold billing codes.
`data/splits/heldout_manifest.csv` is `dataset,encounter_id,split`; ACI-Bench
is `dataset,encounter_id,dialogue,note`.

What is measured, per arm:

| Metric | Definition | Denominator |
|---|---|---|
| Verified rate | `vocab.verified_rate(verified, not_found)` | checkable codes, deduplicated |
| Pessimistic verified rate | `unchecked` counted as not verified | all codes, deduplicated |
| Unchecked share | codes classified `unchecked` | all codes, deduplicated |
| Codes per note | suggested codes after dedup | notes |
| Floor bounds | section 5 | checkable codes |
| Agreement | per-note Jaccard over `(system, normalized code)` pairs | notes where either arm suggested a code |
| Cost | input and output tokens | per arm, summed |
| Latency | wall-clock per call | per arm, p50 and p95 |

The verified rate says a suggested code **exists in the pinned CMS release**.
It does not say the code is **right for the note**.

### The metric's degeneracies, stated rather than discovered

1. **It rewards timidity.** An arm suggesting two obvious diagnoses per note
   scores near 1.0; an arm suggesting eight, including specific and less
   common ones, scores lower. Coverage and specificity are what make a coding
   model useful, and this metric penalizes both. **A verified-rate difference
   accompanied by a materially different codes-per-note is not interpretable
   as a quality difference** (section 2 guard).
2. **`unchecked` is an unscored escape hatch that differs by arm.**
   `vocab.classify` rule 3 returns `unchecked` on a shape test against a
   vocabulary that is not vendored, so a fabricated five-digit number labeled
   CPT costs nothing while a fabricated ICD-shaped code is `not_found`. If
   roughly 45% of codes are `unchecked`, then nearly half of each arm's output
   is exempt from the deciding metric, over structurally different subsets.
   Hence the pessimistic sensitivity analysis above, and the section 2 guard.
3. **Duplicates.** `agent._enrich` does not deduplicate and
   `CodingOutput.verified_count` counts list entries, so a model emitting
   `E11.9` three times contributes three verified codes. Agreement uses sets.
   The two metrics would disagree on what a code is. **All counting in this
   benchmark deduplicates on `(system, normalize(code))` within a note before
   pooling.** This differs from what the shipped agent reports and the
   difference is deliberate and recorded.

Agreement is descriptive context only, never an input to the routing decision.
Two models agreeing says nothing about whether either is right.

---

## 2. The pre-registered decision rule

Committed before the run.

**Equivalence margin: delta = 3.0 percentage points.** This is the largest
verified-rate gap worth ignoring in exchange for the cheaper configuration.

Let `CI` be the 95% interval on the difference in verified rate (arm A minus
arm B) from the paired bootstrap in section 4.

| Condition | Outcome |
|---|---|
| `CI` lies entirely within `(-delta, +delta)` | **Equivalent.** Route on cost and latency. A positive finding. |
| `CI` excludes zero and the point estimate exceeds `delta` | **Difference.** Route on verified rate. |
| Otherwise | **Inconclusive.** The benchmark did not resolve the quality comparison. Say so in the artifact, route on cost, and record that the comparison failed. |

The third branch is the one this design most likely produces, and it exists
because without it the rule cannot fail to reach its own predicted answer: an
underpowered study and two genuinely equivalent configurations both yield a CI
containing zero, and reading those as the same result turns absence of
evidence into evidence of absence. As written, a noisier experiment would more
reliably return "route on cost." That is a rule that rewards its own
measurement error, and it is not pre-registration.

### Guards that void the naive comparison

Checked before the table above is applied:

- **Unchecked divergence.** If the arms' `unchecked_share` differ by more than
  10 percentage points, the two verified rates are computed over structurally
  different subsets and their difference is not attributable to coding
  quality. Report both framings; if the pessimistic and standard verified
  rates disagree on the winner, that disagreement is the finding.
- **Volume divergence.** If codes-per-note differ by more than 25% relative,
  the difference reflects verbosity, not quality (degeneracy 1). Report and
  do not declare a quality winner.
- **Floor divergence.** The floor is arm-specific, not a shared offset
  (section 5). If the difference in floors is of the same magnitude as the
  difference in verified rates, the gap is a labeling or training-cutoff
  artifact, not coding quality.

Whatever the outcome, `shared/llm.py` gets the winning configuration plus a
one-line note, per the roadmap's acceptance criterion.

---

## 3. Input construction: gold reference notes

Each `SoapNote` is built from the ACI-Bench clinician reference note via
`governance/aci_sections.py::bucket_sections`, concatenating each section body
into its `primary` bucket. The P1-2 structuring path is not run, so both arms
receive byte-identical input and any difference is attributable to the coding
configuration rather than to structuring variance.

### The untested assumption is rank invariance

The number this produces is **not** an upper bound on production behavior. The
verified rate is not monotone in input quality: fed a degraded,
Whisper-transcribed, model-structured note, a model plausibly becomes more
conservative and emits fewer, more generic, more common codes, which would
**raise** the verified rate. The direction of the bias is unknown and the spec
does not claim one.

What the design does assume is **rank invariance**: that whichever
configuration wins on clean clinician prose also wins on production-shaped
input. Configurations can differ in robustness to degraded input, and one arm
shares a model family with `ROUTING["structuring"]`, which is a plausible
interaction channel.

**Rank-invariance check.** A paired subset of 15 notes is additionally run
through the real P1-2 structuring path and scored, to confirm the sign of the
difference does not flip. This costs about 12% of the run. A sign flip voids
the routing conclusion and is reported rather than absorbed.

### Fused assessment and plan is a stratification variable, not a footnote

51 of 120 notes fuse `ASSESSMENT AND PLAN`, which `RefSection.primary` reports
as `assessment`, leaving `plan` empty. The earlier claim that this "shifts
labeling, not content" was wrong. All four fields are serialized, so an empty
`plan` string is itself a signal, and `plan` is the section driving procedure
and supply codes, which are disproportionately CPT (routed to `unchecked`) and
HCPCS (checkable). The fused/unfused split can therefore shift denominator
composition and `unchecked_share` between the two note groups, and it is
unknown whether both arms respond identically, which is what matters for the
paired difference.

Metrics are reported per stratum as well as pooled, following the precedent of
`structuring_eval.RunResult.strict_metrics`, which exists to keep exactly this
kind of leniency visible rather than buried.

---

## 4. Sample, estimator, and intervals

**All 120 ACI-Bench held-out encounters.** PriMock57's 7 are excluded on power
grounds. This is a decision, not a constraint: their free-text notes could be
passed as a single `subjective` blob, which is how the repo already handles
them (`structuring_eval.py:292`). They are excluded because 7 notes cannot
move an interval, while noting that a different input distribution is exactly
where the rank-invariance assumption would show strain.

### The estimator

The point estimate is a **pooled ratio of sums**: `verified_rate` called once
over summed counts, per its docstring, which explicitly rejects averaging
per-note rates because that weights a note with one checkable code the same as
a note with twelve and is undefined on the notes that need it least.

### The interval

Codes cluster within notes: a note whose text drives the model toward obscure
codes produces several `not_found` results together, so the effective sample
size is nearer the note count than the code count. Binomial intervals over
codes are therefore wrong.

Intervals come from a **note-level bootstrap with shared indices**:

1. Draw a resample of note indices with replacement, size 120.
2. **Both arms are indexed by the same resampled ids within a replicate.**
3. Within the replicate compute
   `delta_b = (sum_verified_A / sum_checkable_A) - (sum_verified_B / sum_checkable_B)`,
   a difference of ratios-of-sums, matching the point estimate exactly.
4. Repeat 10,000 times. Report BCa intervals.

Step 2 is load-bearing and its failure mode is directional. Bootstrapping each
arm independently discards the positive between-arm correlation induced by
shared note difficulty, widening the interval on the difference, making it more
likely to contain zero, and therefore more likely to return "route on cost,"
which is the answer this spec has already told the reader to expect. A
specification ambiguity whose failure mode confirms the author's prior gets
closed explicitly and tested: **with two identical arms the paired interval
must collapse to a point at zero**, which holds only under shared-index
resampling.

Step 3 must not be a difference of per-note rates. That is a different
estimator from the pooled ratio being reported, and a CI built around it does
not bracket the point estimate.

BCa rather than percentile because percentile coverage on a ratio with roughly
18 events per arm is the least accurate option available. Replicates drawing a
zero denominator get `None` from `verified_rate`, never `0.0`, and must be
handled explicitly rather than passed into a percentile call.

The bootstrap seed and replicate count are recorded in the artifact. Without
them the interval is not reproducible.

### Power, stated honestly

Earlier drafts called `1.96 * SE_diff` the "minimum detectable difference."
That is the **critical value**, the smallest observed difference that would be
declared significant, corresponding to 50% power. A minimum detectable
difference at 80% power is roughly `2.8 * SE_diff`.

The naive binomial figures (about 1.3 points per arm, about 3.5 points
critical value) are wrong in both directions at once: clustering widens the
interval, while the **paired** design narrows it whenever the arms are
positively correlated across notes, which they will be since both see the same
notes. **The net direction is not determined a priori and this spec does not
assert one.** The design effect and the between-arm correlation are computed
from the run and reported with the interval actually obtained.

The metric also sits near a ceiling. With verified rate around 0.94 the
estimand is really the not-found rate, driven by roughly 18 events per arm, so
relative precision is about `1/sqrt(18)`, near 24%. This is a further reason to
expect the equivalent or inconclusive branch.

---

## 5. The floor

Spec 1a of P2-3 gives the verified rate a nonzero floor unrelated to
hallucination, from four causes. **Two are decidable today, a third becomes
decidable with one additional vocabulary pin, and only one needs a human.**

| Cause | Decidable | How |
|---|---|---|
| 4. Degenerate input | yes | fails `_CODE_SHAPE_RE` (defined below) after `normalize` |
| 3. Shape-compatible with CPT, membership unverifiable | partly | `_looks_like_cpt(key)` true and declared system is not `CPT` |
| 2. Real but absent from the pinned release | **yes, with a second pin** | membership in a prior ICD-10-CM release |
| 1. Fabricated | no | residual after the above |

### Cause 2 is decidable, and is plausibly the dominant component

The earlier draft called this a clinical-judgment problem. It is not. It needs
a **second vocabulary pin**. Checking a `not_found` code against one prior
ICD-10-CM release mechanically separates "real code retired or revised since
the model's training data" from "fabricated," with no human involved. Models
trained before FY2026 will emit FY2024 and FY2025 codes, so cause 2 is
plausibly the largest floor component, and resolving it converts most of the
floor band from a human liability into a lookup.

**Scope addition:** vendor ICD-10-CM FY2025 alongside FY2026, same checksummed
loader pattern, same `data/vocab/PROVENANCE.md` treatment. Used only for floor
attribution, never by `classify`, so the production trust boundary is
untouched.

### Cause 3 is an upper bound, not a measurement

`_looks_like_cpt` is a regex on shape (`vocab.py:48`) and CPT is **not
vendored**, so a code matching it cannot be known to be real. A fabricated
five-digit number labeled "ICD-10" satisfies the same test. Classifying such a
code as cause 3 rather than cause 1 deflates the fabrication estimate in the
flattering direction, so the auto-classification is reported as an **upper
bound on cause 3** and never as a count of real mislabeled CPT codes.

### Cause 4 needs a predicate that does not currently exist

`shared/vocab.py` has `normalize` and `_CPT_RE` and no general shape test.
The benchmark defines `_CODE_SHAPE_RE = re.compile(r"^[A-Z0-9]{3,7}$")` locally
and documents it as a benchmark-side heuristic, not a vocabulary rule.

### The floor is arm-specific

The floor is not a shared background offset that cancels in the paired
difference. Cause 3 is a model **labeling behavior** and cause 2 is a function
of each model's **training cutoff**. Both differ by arm. A verified-rate gap
fully explained by a gap in mislabeling or in training recency is not a coding
quality difference. Floors are reported per arm, and the difference in floors
is compared against the difference in verified rates before any winner is
declared (section 2 guard).

### The roster

Emitted as CSV: encounter id, arm, system, code, `model_description`,
auto-classified cause, and a blank adjudication column. The description column
is named `model_description` because the vendored files are codes only, so the
only description available is **the model's own text about its own
`not_found` code**, which is an adjudication-biasing input and is labeled as
such.

With cause 2 mechanized, the residual human adjudication is small and may be
skipped: the run reports a bounded floor either way, with auto-classified
causes as the lower bound and total `not_found` rate as the upper bound.

---

## 6. Storage, artifact, and replay

### Schema

```sql
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS metrics JSONB;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS model_effort TEXT;
```

Idempotent, appended to `db/schema.sql` so it applies to fresh volumes and
existing databases alike. `model_effort` is added because effort is half of
what distinguishes the arms and `eval_runs` is currently the only table
carrying model information without it, unlike `notes` and `agent_decisions`.
Without it the row does not record what was tested.

A coding row leaves `accuracy`, `f1`, `precision`, and `recall` NULL, following
the precedent `evaluate.py::record_structuring_run` documents for PriMock57.
Writing a verified rate into `accuracy` would mislabel it exactly where P3-1,
P3-2, and P3-5 read to build the accuracy trend and the transparency report,
so the mislabeling would propagate into the dashboard automatically.

`evaluate.py::record_structuring_run` accesses `metrics["f1"]` unguarded and
will `KeyError` on a coding payload, so a separate `record_coding_run` writer
is required.

**Phase 3 consumers must be verified to tolerate a row with the whole accuracy
family NULL** before this lands, or the break is merely deferred to P3.

### Artifact and replay

The project's cardinal rule is that every metric is reproducible from a
committed script, and `structuring_eval.py` calls its `replay()` "what makes
the headline number a fact rather than a claim." P2-4 gets the same treatment:
a verdicts-only JSON artifact in `governance/eval_artifacts/`, plus a replay
path that recomputes every number from stored per-code verdicts and errors if
recomputation disagrees with what was stored.

Artifact and `metrics` payload carry, per arm:

```
verified_rate, verified_rate_ci95, verified_rate_pessimistic,
n_notes, n_codes_deduped, n_checkable, n_verified, n_not_found, n_unchecked,
unchecked_share, codes_per_note,
floor_lower, floor_upper, floor_cause_counts, floor_adjudicated,
n_failed, failure_reasons,
strata: {fused: {...}, unfused: {...}},
input_tokens, output_tokens, latency_p50_ms, latency_p95_ms,
requested_model, requested_effort, observed_model,
prompt_version, max_tokens,
vocab_version, vocab_floor_version,
split_digest, bootstrap_seed, bootstrap_replicates
```

`observed_model` is the model id echoed back on the API response, not the
requested one, following the precedent at `structuring_eval.py:246` of taking
the model from the pipeline that actually ran. `split_digest` is carried
because a metric artifact that does not name the split it was scored against
is unmoored. `vocab_version` is carried because the vocabularies are part of
any metric computed on them.

**Replay must hard-error when the stored `vocab_version` differs from the
current `VOCAB_VERSION`.** The vocabulary is correctly absent from the response
cache key, since it does not change what the model writes, but it does change
the verified rate. So a warm-cache re-run under a bumped pin would recompute a
different number from byte-identical responses and look perfectly plausible.

---

## 7. The routing seam

This section replaces a materially wrong earlier version.

### The bug the earlier draft would have shipped

`shared/llm.py::call()` re-reads `ROUTING[component]` internally at line 115.
`agent.run()` reads `ROUTING["coding"]` separately at line 95 and uses it
**only for `log_decision`**. Adding `model`/`effort` parameters to `run()`
alone therefore changes the logged label and not the API call. Both arms would
issue identical Sonnet-5-at-xhigh requests while the artifact recorded one of
them as Opus, producing two near-identical verified rates and confirming this
spec's own stated expectation of "no detectable difference."

That is the worst failure available here: a plausible result, agreeing with the
stated prior, with nothing about the run looking wrong.

### The seam

1. `shared/llm.py::call()` gains optional `model` and `effort` parameters
   defaulting to `ROUTING[component]`. This is the one place model routing
   lives, per CLAUDE.md, so the change belongs here and the spec states it
   rather than describing the work as confined to `agent.py`.
2. `services/agent_coding/agent.py` extracts `parse_and_enrich(raw: str) ->
   CodingOutput` containing the `extract_json`, non-dict, and `ValidationError`
   guards plus `_enrich`. `run()` calls it. The benchmark calls it. One
   parsing path, which is what P2-3's spec section on `classify` exists to
   protect.
3. The benchmark calls `call()` with the arm's explicit `(model, effort)` and
   then `parse_and_enrich`. It does **not** call `run()`.

### Why the benchmark cannot call `run()`

- `AgentInput.encounter_id` is `int` (`schemas.py:33`) and ACI-Bench encounter
  ids are strings such as `D2N068`. There is no integer to supply.
- `run()` unconditionally calls `log_decision`, which INSERTs into
  `agent_decisions` with NOT NULL foreign keys to `encounters(id)` and
  `notes(id)`. Running 120 notes twice would either fail on the constraint or
  require fabricating 240 rows in the production audit table that P2-7 and
  Phase 3 read for drift.
- `run()` consults no cache.

### Tests that would catch the bug

- Assert the **outbound request** carries the override: monkeypatch
  `_client.messages.create` and check `kwargs["model"]` and
  `kwargs["output_config"]["effort"]`. A test asserting only that the default
  still comes from `ROUTING` passes under the bug and is worthless here.
- Assert `observed_model` from the response matches the requested arm, per
  note, and fail the run on mismatch.
- Assert production behavior is unchanged when the overrides are omitted.

---

## 8. Caching, failure policy, and the pilot

### Cache key

The earlier draft justified folding effort into `prompt_version` by claiming
the two arms would otherwise collide. **That was factually wrong.**
`llm_cache.cache_key(task, model, prompt_version, payload)` folds in `model`
at line 25, and the arms differ in model, so that collision cannot occur. The
fix is kept, for the reasons that are real:

- **Within-arm drift.** If `_MAX_TOKENS` or `_SYSTEM` changes between the pilot
  and the run, or between the run and a re-run, an unmodified key produces
  silent **hits** blending two configurations into one number. This is what
  `agent.py:28-33` warns about and is the actual justification.
- **Cross-task collision** on `task` plus model plus payload.

So `version = f"{effort}|{hash_prompt(_SYSTEM)}|max{_MAX_TOKENS}"`, following
`structuring_eval.py:82`.

### Failure policy

Absent from the earlier draft, and its absence would have silently shrunk one
arm's denominator. `_MAX_TOKENS = 5000` was pinned from **Sonnet-at-xhigh**
usage over two notes; Opus at high has a different reasoning-token profile and
may truncate, which `call()` raises as `TruncatedResponseError`.

- Per-arm failure counts and reasons are reported metrics, not silent drops.
- **The analysis set is the intersection** of notes where both arms produced a
  parseable payload, so the paired bootstrap sees the same notes in both arms.
- A single failure does not abort the run, unlike
  `structuring_eval.evaluate_examples`, since one truncated response should not
  cost a 40-to-96-minute run.
- If either arm's failure count exceeds 10% of notes, the run is **void**
  rather than analyzed on a subset.

### Pilot

240 calls at the 10 to 24 seconds observed in P2-3 is 40 to 96 minutes serial.
A 5-note pilot runs first, reporting **token usage, latency, and truncation
only**.

**The pilot draws from the train split, not the held-out set, and does not
compute a verified rate.** Pilot notes would otherwise be held-out notes whose
responses are cached and carried into the full run unchanged, so deciding
whether to proceed after seeing their outcomes would condition on partial
outcome data from inside the analysis set. That is an optional-stopping
violation, and it is free to avoid: the pilot's real purpose is token budget,
truncation risk, and replacing section 4's codes-per-note projection, none of
which needs held-out data.

The Batch API would halve cost and the guide endorses it for offline
re-scoring, but it is deliberately not used: it adds an asynchronous path to
`shared/llm.py` under a task whose purpose is a trustworthy number. Revisit if
the pilot shows cost is prohibitive.

Cost is reported in **tokens, not currency**. No current pricing table is
available in this environment and an invented one would be the same class of
error this re-scope exists to prevent.

---

## 9. Acceptance criteria

- [ ] `call()` accepts `model`/`effort` overrides; test asserts the **outbound
      request** carries them, and that omitting them preserves `ROUTING`
- [ ] `parse_and_enrich` extracted; `run()` and the benchmark share it
- [ ] `observed_model` recorded per note and mismatch fails the run
- [ ] `ALTER TABLE` for `metrics` and `model_effort`, idempotent against an
      existing database
- [ ] `record_coding_run` writer added; `record_structuring_run` untouched
- [ ] Phase 3 consumers verified against an all-NULL accuracy-family row
- [ ] Cache `prompt_version` folds in effort, prompt hash, and `max_tokens`
- [ ] Counting deduplicates on `(system, normalize(code))` within a note
- [ ] Bootstrap uses shared indices; test asserts identical arms give an
      interval collapsing to zero
- [ ] Bootstrap is BCa, seed and replicate count recorded, `None` denominators
      handled
- [ ] Pilot drawn from train, reports cost and latency only
- [ ] Analysis set is the intersection; per-arm failure counts reported; run
      voids above 10% failures
- [ ] ICD-10-CM FY2025 vendored and checksummed for floor attribution only
- [ ] Floor reported per arm as bounds, with cause 3 labeled an upper bound
- [ ] Metrics reported per fused/unfused stratum as well as pooled
- [ ] Rank-invariance check on 15 notes through the real P1-2 path
- [ ] Artifact committed and a replay path recomputes every number, hard-erroring
      on a `vocab_version` mismatch
- [ ] Decision rule applied as written, including the inconclusive branch, and
      the winning **configuration** recorded in `shared/llm.py`
- [ ] No artifact describes any number as coding accuracy, or attributes a
      result to a model rather than a configuration
