# P2-4: Coding Model Routing Benchmark

**Status:** design, pending review
**Roadmap task:** P2-4, as re-scoped in commit `f1cb3ab`
**Depends on:** P2-3 (merged, `7fdd02f`), P0-5 held-out split

---

## 1. What this measures, and what it does not

This benchmark decides which model `shared/llm.py::ROUTING["coding"]` should
name. It compares Sonnet 5 at xhigh against Opus 4.8 at high.

**It does not measure coding accuracy, and no artifact it produces may be
described that way.** Neither held-out dataset carries gold billing codes.
`data/splits/heldout_manifest.csv` is `dataset,encounter_id,split`, and
ACI-Bench's challenge CSVs are `dataset,encounter_id,dialogue,note`. There is
nothing to compute precision or recall of *correct* codes against.

What is measured, per arm:

| Metric | Definition | Denominator |
|---|---|---|
| Verified rate | `shared/vocab.py::verified_rate(verified, not_found)` | checkable codes (excludes `unchecked`) |
| Unchecked share | codes classified `unchecked` | all suggested codes |
| Agreement | per-note Jaccard over normalized code sets | notes where at least one arm suggested a code |
| Cost | input and output tokens | per arm, summed |
| Latency | wall-clock per call | per arm, p50 and p95 |

The verified rate says a suggested code **exists in the pinned CMS release**.
It does not say the code is **right for the note**. A model that suggests
`E11.9` for every encounter scores 1.0. This distinction is the whole reason
the roadmap was re-scoped, and it belongs in every artifact this produces.

Agreement is reported as descriptive context only. It is not an input to the
routing decision, because two models agreeing tells you nothing about whether
either is correct.

---

## 2. The pre-registered decision rule

Committed here, before the run, so the winner cannot have a story written
around it afterward.

> Compute a 95% confidence interval on the **difference** in verified rate
> between the two arms, by note-level bootstrap (section 4). If that interval
> contains zero, declare no detectable difference and route on cost and
> latency, preferring the cheaper arm. If it excludes zero, route on verified
> rate.

**"No detectable difference, routed on cost" is an anticipated and legitimate
outcome, not a failed experiment.** At the sample size available it is the
more likely result. Recording it as a real finding is the point of writing
the rule down first.

Whatever the outcome, `shared/llm.py` gets the winner plus a one-line note on
why, per the roadmap's acceptance criterion.

---

## 3. Input construction: gold reference notes

Each `SoapNote` is built from the ACI-Bench clinician reference note via
`governance/aci_sections.py::bucket_sections`, concatenating each section's
body into its `primary` bucket. The P1-2 structuring path is **not** run.

Both arms therefore receive byte-identical input, so any difference in output
is attributable to the coding model rather than to structuring variance or to
whichever structuring model happened to run.

**Stated limitation.** This measures the coding model on clean clinician-written
input, which is not the production input shape. Production feeds it
Whisper-transcribed, model-structured notes carrying their own error. The
number this produces is therefore an upper bound on production behavior and
must never be presented as end to end.

**Known distortion.** 51 of the 120 held-out notes fuse `ASSESSMENT AND PLAN`
into one section, which `RefSection.primary` reports as `assessment`, leaving
`plan` empty. All four `SoapNote` fields are serialized to the model by
`agent.run` via `inp.soap.model_dump_json()`, so the model sees the same text
either way. This shifts labeling, not content, and is not expected to move the
metric. It is recorded so a reviewer does not discover it and assume it was
missed.

---

## 4. Sample and statistical power

**All 120 ACI-Bench held-out encounters. PriMock57's 7 are excluded**, because
their notes are free-text GP shorthand with no section headers, so
`bucket_sections` raises `UnknownSectionError` on them by design. Seven
examples would not move any interval regardless.

The unit of analysis is the **checkable code**, not the note. P2-3's live run
observed roughly 4.5 codes per note with about 55% checkable, which projects
to roughly 300 checkable codes per arm at n=120. That projection comes from
two notes and is itself uncertain; the pilot in section 7 replaces it with a
measurement before the full run is committed.

### Codes are clustered within notes, so binomial intervals are wrong

A naive binomial standard error over ~300 codes gives about 1.3 points at
p=0.95, and about 3.5 points for the minimum detectable difference between
two arms. **Those figures understate the true uncertainty.** Codes drawn from
the same note are correlated: a note whose text drives the model toward
obscure codes produces several `not_found` results together, so the effective
sample size is nearer the note count than the code count.

Rather than estimate a design effect and argue about it, intervals are
computed by **note-level bootstrap**: resample the 120 notes with replacement
10,000 times, recompute the pooled verified rate within each resample, and
take the 2.5th and 97.5th percentiles. Resampling whole notes preserves the
within-note correlation instead of assuming it away. The same procedure,
applied to the paired per-note difference, produces the interval the section 2
rule tests against zero.

The realistic minimum detectable difference is therefore expected to be wider
than 3.5 points, plausibly 4 to 5. This is a further reason to expect and
accept the "route on cost" branch.

---

## 5. The floor measurement

Spec 1a of P2-3 gives the verified rate a nonzero floor unrelated to
hallucination, from four causes. Three are decidable in code:

| Cause | Decidable? | How |
|---|---|---|
| 4. Degenerate input | yes | empty, whitespace, or not code-shaped after `normalize` |
| 3. Real CPT mislabelled | yes | `vocab._looks_like_cpt(key)` true and declared system is not `CPT` |
| 1. Fabricated | **no** | requires clinical judgment |
| 2. Real but absent from the pinned release | **no** | requires clinical judgment |

Causes 1 and 2 are indistinguishable to this repo, which pins exactly one
release pair. Separating them is a human adjudication over the `not_found`
roster, and only over that roster.

**The run produces a bounded floor whether or not the adjudication happens.**
Auto-classified causes 3 and 4 give a lower bound; the total `not_found` rate
gives an upper bound. Adjudication narrows the band between them. An
unadjudicated result is reported as an interval and is still strictly more
than the nothing available today, since P2-3's Task 13 produced zero
`not_found` codes over two notes and therefore measured no floor at all.

The harness emits a `not_found` roster as CSV with encounter id, system, code,
description, auto-classified cause where known, and a blank column for
adjudication.

---

## 6. Storage

`eval_runs` carries only `accuracy`, `f1`, `precision`, and `recall`, all of
which are accuracy-family. Writing a verified rate into `accuracy` would
mislabel it at the exact place P3-1, P3-2, and P3-5 read to build the accuracy
trend chart and the transparency report, so the mislabeling would propagate
into the dashboard automatically.

Schema change, idempotent, appended to `db/schema.sql` so it applies to both
fresh volumes and existing databases:

```sql
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS metrics JSONB;
```

A coding row leaves `accuracy`, `f1`, `precision`, and `recall` NULL and puts
everything in `metrics`. This extends a convention the repo already has:
`governance/evaluate.py::record_structuring_run` documents leaving `accuracy`
NULL for PriMock57 rather than filling it with a number the column name would
misdescribe.

`metrics` payload per arm:

```json
{
  "verified_rate": 0.94,
  "verified_rate_ci95": [0.91, 0.97],
  "n_notes": 120,
  "n_codes": 540,
  "n_checkable": 297,
  "n_verified": 279,
  "n_not_found": 18,
  "n_unchecked": 243,
  "unchecked_share": 0.45,
  "floor_lower": 0.01,
  "floor_upper": 0.06,
  "floor_adjudicated": false,
  "input_tokens": 96000,
  "output_tokens": 216000,
  "latency_p50_ms": 12000,
  "latency_p95_ms": 24000,
  "vocab_version": "ICD-10-CM FY2026 (2026-04-01) + HCPCS Level II 2026Q3"
}
```

`vocab_version` is carried because the vocabularies are part of any metric
computed on them. If either pin moves between two benchmark runs, the verified
rate moves for reasons unrelated to the model under test.

---

## 7. Code layout, the routing seam, and cost control

New `governance/coding_benchmark.py` and `scripts/run_coding_benchmark.py`,
mirroring the `structuring_eval.py` and `run_structuring_eval.py` pair.

### The routing seam

`agent.run()` reads `ROUTING["coding"]` and so always uses one model. The
benchmark needs both arms through the **same** parsing and enrichment path.
Reimplementing the parsing guards in the harness is precisely the divergence
P2-3's spec section on `classify` exists to prevent.

`run()` therefore gains optional `model` and `effort` parameters defaulting to
`ROUTING["coding"]`. Production behavior is unchanged when they are omitted.
This is a deliberate modification to code merged one commit ago, and it is the
smallest change that keeps one parsing path.

### Caching

`governance/llm_cache.py::cache_key` takes `(task, model, prompt_version,
payload)` and folds in neither effort, nor the prompt hash, nor `max_tokens`.
The benchmark must build `prompt_version` the way
`governance/structuring_eval.py:82` does:

```python
version = f"{effort}|{hash_prompt(_SYSTEM)}|max{_MAX_TOKENS}"
```

Without this, the two arms differ only by effort, which is invisible to the
key, and the second arm would read the first arm's cached responses. That
failure produces two identical verified rates and a confident, wrong
conclusion that the models are indistinguishable.

### Pilot before the full run

240 calls at the 10 to 24 seconds observed in P2-3 is 40 to 96 minutes serial.
A **5-note pilot across both arms, 10 calls**, runs first and reports token
usage, verified rate, and latency. The full run is committed only after the
pilot's numbers are seen.

The Batch API would halve cost and the guide endorses it for offline
re-scoring, but it is deliberately **not** used here. It adds an asynchronous
code path to `shared/llm.py` under a task whose entire purpose is producing a
trustworthy number, which is the wrong place to introduce a new failure mode.
Revisit if the pilot shows cost is prohibitive.

Cost is reported in **tokens, not currency**. No current pricing table is
available in this environment, and an invented one would be the same class of
error this whole re-scope exists to prevent.

---

## 8. Acceptance criteria

- [ ] `ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS metrics JSONB` applied
      and idempotent against an existing database
- [ ] `run()` accepts `model`/`effort` overrides, production path unchanged,
      covered by a test asserting the default still comes from `ROUTING`
- [ ] Cache key folds in effort, prompt hash, and `max_tokens`, covered by a
      test asserting the two arms produce different keys for identical input
- [ ] Pilot run reported before the full run is started
- [ ] Both arms written to `eval_runs` with `accuracy`/`f1`/`precision`/`recall`
      NULL and a populated `metrics`
- [ ] `not_found` roster emitted as CSV with auto-classified causes
- [ ] Floor reported as a bounded interval, adjudicated or not
- [ ] Decision rule from section 2 applied as written, and the winner recorded
      in `shared/llm.py` with a one-line note
- [ ] No artifact describes any number as coding accuracy
