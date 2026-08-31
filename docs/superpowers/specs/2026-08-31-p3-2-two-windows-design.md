# P3-2: Two Windows of Data

**Status:** design, awaiting review
**Roadmap task:** P3-2
**Depends on:** P3-1 (guarded writer, `GenerationConfig`, window semantics)
**Model/effort:** see section 0. The guide says Sonnet 5 at medium; this spec
argues that is wrong for this task.

---

## 0. The guide's recommendation, and why it is wrong here

`docs/MODEL-EFFORT-GUIDE.md` line 74 rates P3-2 **Sonnet 5 at medium, "Data
plumbing"**. That rating was written before P3-1 defined what a window is, and
it is wrong on both counts.

P3-2 does not plumb data. It **produces a second measured headline number on
the held-out set**, and P3-3's entire drift metric is computed from the gap
between it and window 1. The guide's own closing rule settles it:

> if the task writes or protects a number that could end up on your resume, use
> `/model opus` and `/effort max`.

More decisively: this task has a live trap that silently fabricates that number
(section 2). Running it at medium effort is how the trap gets sprung.

**Recommendation: keep `/model opus` and `/effort max`.** The guide's row for
P3-2 should be corrected to O48/max as part of this task, with the reason
recorded, rather than left to mislead the next reader.

---

## 1. The gate

> **P3-2 Two windows of data.** Done when at least one agent has accuracy stored
> for at least two distinct versions or time windows, so a trend exists to plot.

Note "has **accuracy** stored". Only the ACI-Bench path stores a non-NULL
`accuracy` (section placement); PriMock57's is NULL by construction because
placement is not scorable against an unsectioned GP note. So the gate is
satisfied by an **ACI-Bench window 2**, and a PriMock57 window alone would not
satisfy it however cheap it is.

---

## 2. The blocking finding: the harness will fabricate window 2 if asked today

**Measured 2026-08-31, free, no API calls:**

```
current structuring config
  model   claude-sonnet-5
  effort  high
  prompt  b7b42093e9a7
  max     8000
  version high|b7b42093e9a7|max8000

ACI held-out structuring calls: 120/120 ALREADY CACHED
```

`governance/.cache` holds 2,804 entries, 11 MB, from the July runs.
`cache_key(task, model, prompt_version, payload)` **does not include the
window**, and P3-1 fixed window semantics as "a point in time with the
generation configuration held fixed". Two windows are therefore, by
construction, two cache keys that are identical.

Running `scripts/run_structuring_eval.py --dataset aci --window-label <new>`
today would:

1. hit the cache for all 120 structuring calls and replay July's generated notes;
2. hit the cache for the judge calls too, since their payloads are those same notes;
3. produce metrics **bit-identical** to window 1;
4. write an `eval_runs` row stamped today, with `f1 = 0.8685633622463043`;
5. pass `replay()`, pass P3-1's guard, and pass CI.

The result is two points on a trend that are one measurement, and P3-3 would
then be "validated" against a pair of identical points, which demonstrates
nothing. **Nothing in the system today detects this.** It is the same class of
failure as the coding metric being called accuracy: a number that is
reproducible, well-formed, and means something other than what it appears to.

This is what P3-2 is actually about. The paid run is the easy half.

---

## 3. What window 2 measures, and what it must not blend

With the configuration held fixed, the difference between window 1 and window 2
is **change on the vendor's side of a hosted model**. That is the reading P3-3
and the HTI-1 transparency framing need.

For that reading to hold, a window must be a coherent snapshot: every hosted
model call in it taken at the same time, under the same configuration. A window
whose generation is fresh but whose judging is replayed from July is a blend of
two experiments, which is precisely what `governance/llm_cache.py`'s own
docstring says its key exists to prevent:

> if a judge prompt were edited or the judge model swapped and the old verdicts
> were silently reused, the reported number would be a blend of two different
> experiments.

**The rule this spec adopts:**

| Artifact | Scope | Why |
|---|---|---|
| Whisper transcripts | **shared** across windows | Local, deterministic, not a hosted call. The audio does not change and Whisper is not under test. Re-running costs 2+ hours of CPU and buys nothing. |
| Structuring generation | **window-scoped** | The subject of the measurement |
| Reference decomposition | **window-scoped** | A hosted call. Sharing it would make the window a partial snapshot |
| Presence and support judging | **window-scoped** | A hosted call, and its payload depends on generation anyway |

Reference decomposition is roughly 480 Haiku calls that arguably need not be
re-paid. They are re-paid on purpose: coherence of the snapshot is worth more
than the saving, and a rule with one exception carved into it for cost is a rule
that erodes.

---

## 4. Design

### 4a. Window-scoped cache namespaces

```
governance/.cache/                    <- unchanged. July's runs, P2-4's coding run
governance/.cache/windows/<label>/    <- every window from P3-2 on
```

Non-destructive by choice. The existing flat cache is **not migrated**: it holds
P2-4's coding entries alongside the structuring ones, and the memory of this
project already records a run lost to a cache key changed under a live run. The
asymmetry (window 1 at the root, later windows in subdirectories) is documented
rather than tidied, because tidying it risks the one thing that must not break.

`scripts/run_structuring_eval.py` gains `--cache-namespace`, defaulting to the
window label. Whisper transcription keeps a `Cache` pointed at the shared root.

### 4b. Two independent guards against a fabricated window

Belt and braces, in keeping with how P2-4's floor and P2-6's reducer were
handled. The two guards fail for different reasons, so neither masks the other.

**Guard 1, causal: a fresh window must not be served from cache.** The harness
counts cache hits and misses per task type and records them in the artifact.
Filing a window whose structuring calls were served from cache is refused. This
catches the actual mechanism, including the case where someone points
`--cache-namespace` at an existing window.

**Guard 2, symptomatic: two windows may not have identical counts.** At file
time, a window whose five `StructuringCounts` tallies exactly match an existing
row for the same agent and dataset under a **different** window label is
refused. Across 120 encounters and 6,550 reference facts, exact agreement on all
five integers is not something independent generation produces; it is proof of a
replay or a double-filing.

Scoping guard 2 to a *different* label is what keeps `scripts/refile_eval_run.py`
working: re-filing is by definition the same measurement under the same label,
and that stays legal.

### 4c. Token and cost accounting

`shared/llm.py::call` already delegates to `call_detailed`, which already
carries `input_tokens` and `output_tokens`. An opt-in usage recorder in
`call_detailed` therefore gives every call site accounting for free, including
the structuring path, which has never had it. The run reports its own token
totals and cost, and both land in the artifact.

This is not scope creep: P3-2 must be able to state what window 2 cost, and
there is currently no mechanism that can. P4-3 and P4-5 need the same thing.

**`governance/pricing.json` has no entry for `claude-haiku-4-5-20251001`**, and
the judge is Haiku. The rate must be read from the published docs and added by
hand. That file's own note says it is a committed **input** and is never
generated from a model response, so the number will be looked up and cited, not
recalled from memory.

---

## 5. Cost: unknown, and measured before it is spent

A cold ACI-Bench window is roughly **120 Sonnet 5 calls at high effort** plus
roughly **1,100 Haiku judge calls**. The Sonnet half dominates and its output
token count is not knowable in advance, because effort-driven reasoning tokens
are billed as output and are not visible in the cached response text. P2-4
learned this the expensive way: Sonnet 5 at xhigh emitted about 5.3x the output
tokens of Opus 4.8 at high, which inverted the expected cost ranking.

**So P3-2 follows P2-4's precedent and runs a pilot first.** Five held-out
encounters, cold, in a scratch namespace, with real usage recorded. Extrapolate
to 120, present the figure, and **spend nothing further without explicit
approval**. P2-4's pilot caught a run-invalidating `max_tokens` truncation
before the full spend; that is the entire argument for doing it again.

**There is no pricing deadline. An earlier draft of this spec said there was,
and it was wrong.** `governance/pricing.json` recorded Sonnet 5 at $3/$15 with
a note that $2/$10 was introductory "through 2026-08-31", which made today look
like the last cheap day. Checking the published table instead of trusting the
committed file showed the opposite:

> The $2/$10 per million input/output token pricing for Claude Sonnet 5,
> announced at launch as introductory pricing through August 31, 2026, is now
> the standard price. The previously scheduled increase to $3/$15 per million
> input/output tokens on September 1, 2026 will not occur.

So the run costs the same today as next week, and the decision can be taken on
its merits. Two corrections follow, both made in this task:

1. **`governance/pricing.json` is updated** to Sonnet 5 at $2/$10, with
   `claude-haiku-4-5-20251001` added at $1/$5 (the pinned judge, previously
   unpriced, which is why the structuring harness could not cost itself).
2. **P2-4's recorded costs were computed under a rate that never took effect.**
   Recomputed from that artifact's own token counts through
   `governance/pricing.py`:

   | arm | in / out tokens | recorded | at current rates |
   |---|---|---|---|
   | A, sonnet-5 xhigh | 144,012 / 371,889 | $6.01 | **$4.01** |
   | B, opus-4-8 high | 144,012 / 97,677 | $3.16 | **$3.16** |

   Opus 4.8 at high stays cheaper, so **the P2-4 routing branch does not
   flip**, but the margin narrows from $2.85 to $0.84. The reported P2-4
   figures are left as they stand, because they are what that run measured
   under the table of the day; the correction lives in `pricing.json`'s note
   and here.

The general point, which is the reusable one: **a committed price table is an
input that goes stale silently.** Nothing in the repo would have noticed, and a
cost-based routing decision sits on top of it.

---

## 6. Files

| File | Change |
|---|---|
| `governance/structuring_eval.py` | window-scoped cache, hit/miss counters, usage totals in the artifact |
| `governance/eval_runner.py` | guard 2 (identical counts), cache-hit refusal |
| `governance/evaluate.py` | counts lookup for existing windows |
| `shared/llm.py` | opt-in usage recorder in `call_detailed` |
| `governance/pricing.json` | add Haiku 4.5, rate looked up and cited |
| `scripts/run_structuring_eval.py` | `--cache-namespace`, pilot mode, cost report |
| `scripts/refile_eval_run.py` | unchanged; guard 2 is scoped so it stays legal |
| `docs/MODEL-EFFORT-GUIDE.md` | correct P3-2 to O48/max, with the reason |
| `tests/test_eval_windows.py` | new |
| `docs/ROADMAP.md` | P3-2 evidence entry |

---

## 7. Testing

CI-runnable, no dataset and no database:

- a run whose structuring calls were cache hits is refused at file time
- two artifacts with identical counts under different window labels: the second
  is refused
- the same artifact re-filed under the **same** label is allowed, so
  `refile_eval_run.py` does not regress
- the window cache namespace isolates: a key written under window A is not
  visible to window B, and a Whisper key is visible to both
- the usage recorder totals tokens across calls and is off by default

Behind `needs_db`: guard 2 against real rows, including that a refused filing
inserts nothing.

**Mutation check, in the style of P2-6's reducer:** with the window namespace
removed so both windows share a cache, the fresh-window test must fail. A guard
that passes whether or not the isolation exists is not a guard.

---

## 8. Known gaps, stated rather than discovered later

1. **Window 1's cache lives at the root, later windows in subdirectories.** An
   accepted asymmetry, for the reason in 4a.
2. **Guard 2 compares counts, not content.** Two genuinely different runs that
   happened to produce identical tallies would be wrongly refused. The
   probability is negligible and the failure is loud and manual to override,
   which is the right direction to be wrong in.
3. **Window 2 measures vendor drift confounded with sampling.** Effort-driven
   reasoning calls sample; two runs of the same configuration differ somewhat
   even with no vendor change. P3-3 must not read a small gap as drift, and
   P3-2 does not establish that gap's size. Establishing it would need a
   same-day repeat run, which is a second full spend and is **not** in P3-2.
   This is the single most important caveat to carry into P3-3.
4. **No accuracy is claimed for any agent P3-1 refused.** Unchanged here.
