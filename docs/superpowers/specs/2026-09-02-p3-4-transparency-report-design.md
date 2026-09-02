# P3-4: Transparency Report Generator

**Status:** design, awaiting review
**Roadmap task:** P3-4
**Depends on:** P2-2 (`CareGapSource` citations), P2-4 (coding routing benchmark),
P3-1 (`SCOREABLE`/`UNSCOREABLE`, `eval_runs`), P3-3 (`governance/drift.py`)
**Model/effort:** Sonnet 5 at high, per `docs/MODEL-EFFORT-GUIDE.md` line 76:
"Verify HTI-1 field mapping by hand."

---

## 1. The gate

> **P3-4 Transparency report generator.** Done when `governance/transparency.py`
> produces a report from real `model_inventory` data using ONC HTI-1 style
> fields, mapped to real disclosure language where possible.

`model_inventory` exists in `db/schema.sql` (`agent_name`, `model`, `version`,
`intended_use`, `training_data_note`, `known_limitations`) but no code has ever
written a row to it. The stub `governance/transparency.py` is a single
six-line `SELECT`. This task starts from zero real rows and a report shape
that predates any decision about what "HTI-1 style" means for a project this
size.

## 2. What the real rule actually says, verified rather than assumed

The Federal Register page for the HTI-1 final rule (89 FR 1192, 2024-01-09)
blocks automated fetching. AHIMA's summary of the rule's predictive-DSI source
attributes, fetched directly, gives the 31 required source attributes grouped
into nine categories, quoted:

> Details and output of the DSI; Purpose of the intervention; Cautioned
> out-of-scope use of the intervention; Intervention development details and
> input features; Process used to ensure fairness in development of the
> intervention; External validation process; Quantitative measures of
> performance; Ongoing maintenance of intervention implementation and use; and
> Updates and continued validation or fairness assessment schedule.

This spec maps to those nine categories by name. It does not claim to
reproduce the literal 31 sub-attributes or to constitute HTI-1 certification;
the gate itself says "HTI-1 **style**," not "HTI-1 compliant." Where the
category is a number this project already measures, it comes from a live
query (section 5), never from typed text that can go stale.

## 3. Schema: four new columns, named after the categories they answer

```sql
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    cautioned_out_of_scope_use TEXT;
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    fairness_process_note TEXT;
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    external_validation_note TEXT;
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    maintenance_schedule TEXT;
```

The existing three columns keep their names and cover three more categories:
`intended_use` -> Purpose of the intervention, `training_data_note` ->
Intervention development details and input features, `known_limitations` ->
overlaps Cautioned out-of-scope use for agents where the two read the same
(coding, prior_auth), split apart where they don't (care_gap: see section 4).

That accounts for six of nine categories as stored text. The remaining three,
**Details and output of the DSI**, **Quantitative measures of performance**,
and **Updates and continued validation or fairness assessment schedule**, are
answered by the live join in section 5, not by a column, because they are
numbers `eval_runs` and `governance/drift.py` already compute. Storing them as
static text a second time is the exact failure this project has already paid
for twice: `governance/pricing.json` priced a rate that never took effect, and
the P1-4 cache key silently blended prompt versions. A committed value goes
stale silently; a query does not.

## 4. Seeding: `scripts/seed_model_inventory.py`

One row per **clinical** agent: `note_structuring`, `coding`, `care_gap`,
`prior_auth`. `transparency` and `eval_judge` are excluded, being infrastructure
with no `agent_decisions` rows and no decision a clinician sees, not decision
support interventions in the sense the rule means.

`model` and `version` are read from `shared.llm.ROUTING`, never hand-typed, so
a routing change is never silently unreflected in the disclosure. `version` is
the model string itself (`claude-opus-4-8`, `claude-sonnet-5`,
`claude-haiku-4-5-20251001`); there is no separate semantic version for a
hosted model in this project.

Upserts on `(agent_name, model, version)`, the table's existing unique
constraint, so re-running the script after a routing change adds a new row
rather than mutating history, and old rows stay queryable by whichever window
they were current for.

Text is sourced from language this project has already committed and vetted,
not freshly authored:

**`coding`**
- `known_limitations` / `cautioned_out_of_scope_use`:
  `governance.evaluate.UNSCOREABLE["coding"]` verbatim. A test asserts this
  string equality directly, so the two can never drift apart.
- `external_validation_note`: the P2-4 result. Routed to `claude-opus-4-8` at
  high **on cost**, paired delta 0.70 points, 95% BCa CI `[-0.73, 2.22]`,
  **straddling zero**. Written as "not a demonstrated quality win over the
  alternative benchmarked; the routing decision was cost, not accuracy" so the
  report cannot be misread as a validated accuracy claim.
- `maintenance_schedule`: `None`. No re-benchmark cadence is defined anywhere
  in this project yet. A comment says so; nothing is invented to fill it.

**`care_gap`**
- `known_limitations`: `UNSCOREABLE["care_gap"]` verbatim ("deterministic and
  unit-tested, which is a correctness property, not a measured accuracy").
- `cautioned_out_of_scope_use`: the module docstring's own caution, verbatim
  where practical, including the LIPID_SCREENING specific: "a keyword scan
  cannot evaluate [the USPSTF grade B 10-year CVD risk threshold], so a fired
  gap does not mean the graded recommendation applies to this patient."
- `fairness_process_note`: the four `CareGapSource` citations from
  `services/agent_care_gap/rules.py` (organization, title, grade, year, URL),
  because for a deterministic rules engine, "every fired rule traces to a
  graded guideline, verified 2026-07-16" is a stronger and more concrete
  fairness-process claim than prose about a training process that does not
  exist here.

**`note_structuring`**
- `training_data_note`: the asymmetric scoring convention from
  `score_structuring`'s docstring, recall against the clinician note,
  precision against the transcript, stated in one sentence.
- `external_validation_note` stays thin by design: the live join in section 5
  is the real answer for this agent, which has an actual held-out set.
- `cautioned_out_of_scope_use`: PriMock57's n=7 and NULL accuracy, per P3-2's
  "too small to quote as a headline."

**`prior_auth`**
- Shortest row, matching how little this codebase currently asserts:
  `UNSCOREABLE["prior_auth"]` for both `known_limitations` and
  `cautioned_out_of_scope_use`. `fairness_process_note` and
  `maintenance_schedule` are `None`.

Any field with nothing truthful to say is written as an explicit `None` with a
one-line code comment explaining why, never a plausible-sounding placeholder.

## 5. `governance/transparency.py`: the live join

```python
def build_report() -> list[dict]:
```

For each `model_inventory` row: query `eval_runs` for that `agent_name`,
ordered by `measured_at` descending, limit 2. Build the report row's
performance and validation categories from what comes back:

- **0 rows**: `"not yet measured"` for both quantitative performance and the
  validation/maintenance category. Never a missing key, matching P3-1's rule
  that a declined metric is stated, not omitted.
- **1 row**: quantitative performance is that row's metrics (`f1`, `precision`,
  `recall`, `accuracy`, all `None`-safe); validation category is `"only one
  window filed; no drift comparison possible yet"`.
- **2+ rows, same `dataset_ref`**: performance is the most recent row's
  metrics. Validation/maintenance is the result of
  `governance.drift.compare_structuring_windows(older, newer, replicates=2000)`
  read from the two artifacts those rows' `provenance.artifact` point to,
  reported as verdict plus every caveat. This is where windows 7 and 25
  produce `NOT_ATTRIBUTABLE`, printed with its three named reasons, exactly as
  `scripts/run_drift_check.py` reports them. 2,000 replicates, not P3-3's
  full 10,000, since this runs on every report build rather than being a
  one-off measurement, matching the replicate count P3-3's own tests use for
  the same reason.
- **2+ rows, different `dataset_ref`**: `NOT_COMPARABLE`, same as
  `compare_structuring_windows` would return, stated rather than silently
  picking one.
- Drift comparison only runs for `note_structuring`, the only agent with a
  paired-comparable metric today. Other agents' "ongoing maintenance" category
  reads `"no measured accuracy exists to compare across windows; see
  cautioned_out_of_scope_use"`.

Each report row is a flat dict, keyed by the nine category names verbatim
(`"Purpose of the intervention"`, `"Quantitative measures of performance"`,
etc.), not by column name, so a reader checking this report against the rule
does not need to cross-reference the schema. `build_report()` takes no
arguments.

## 6. Testing, all free

`tests/test_transparency.py`, `needs_db = pytest.mark.skipif(not
_db_reachable(), ...)` mirroring `test_registry.py`, since this is the first
governance module that reads back what it wrote rather than working purely
off committed JSON artifacts.

- every one of the 9 category keys is present for all 4 seeded agents, value
  either non-empty or explicitly `None` with a reason recorded in the seed
  script's comments (checked by presence of the key, not by asserting content
  for the `None` cases)
- `coding`'s `Cautioned out-of-scope use` equals `UNSCOREABLE["coding"]`
  exactly, so the two cannot drift apart
- an agent with zero `eval_runs` rows reports `"not yet measured"`, not a
  missing key or a crash
- an agent with one `eval_runs` row reports the single-window message, not a
  drift comparison
- `note_structuring` against the two real committed artifacts produces the
  same verdict and delta `scripts/run_drift_check.py` prints for those same
  two artifacts, so the two paths cannot silently disagree
- re-running `seed_model_inventory.py` twice does not duplicate rows
  (upsert, not insert)

## 7. Out of scope, deliberately

- **No rendering.** `build_report()` returns structured data. P4-1 renders it
  in the dashboard; this task does not produce HTML, PDF or Markdown output.
- **No new API endpoint.** P3-5 exposes this over HTTP; this task is the
  function P3-5 will call.
- **No `transparency`/`eval_judge` rows.** Infrastructure, not a decision
  support intervention a clinician sees.
- **No claim of HTI-1 certification.** "Style," per the gate's own wording.
