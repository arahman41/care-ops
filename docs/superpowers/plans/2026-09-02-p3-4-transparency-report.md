# P3-4 Transparency Report Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `governance/transparency.py::build_report()` produces one report row per clinical agent from real `model_inventory` data, using ONC HTI-1's nine source-attribute categories by name, with performance and validation answered live from `eval_runs` and `governance/drift.py` rather than stored as text that can go stale.

**Architecture:** A schema migration adds four columns to `model_inventory` for the categories that have no query behind them. `scripts/seed_model_inventory.py` upserts one row per clinical agent, sourced from language this project has already committed (`UNSCOREABLE`, the P2-2 rule citations, the P2-4 result). `build_report()` joins each row against `eval_runs`, filtered to that row's own `model`, and runs `compare_structuring_windows` when two same-dataset windows exist for `note_structuring`.

**Tech Stack:** Python 3.10, psycopg 3, existing `governance.drift` and `governance.evaluate`.

**Spec:** `docs/superpowers/specs/2026-09-02-p3-4-transparency-report-design.md`

**Branch:** `p3-4-transparency-report` (already created)

**Run tests with:** `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest <args>` from
`care-ops-copilot/`. Postgres is reachable in this environment (verified), so
`needs_db` tests run rather than skip; if it is ever down, they skip cleanly
per the `_db_reachable()` idiom in `tests/test_registry.py`.

---

## A correction to the spec, made while planning

The spec's section 5 says the live join queries `eval_runs` for an agent
"ordered by `measured_at` descending, limit 2." Checked against the real
rows before writing code:

```
id  agent_name        model              dataset_ref              created_at
3   coding             claude-sonnet-5    aci-bench-heldout-v1     2026-08-07 21:45:51.233
4   coding             claude-opus-4-8    aci-bench-heldout-v1     2026-08-07 21:45:51.267
7   note_structuring   claude-sonnet-5    aci-bench-heldout-v1     2026-07-14 03:24:03
8   note_structuring   claude-sonnet-5    primock57-heldout-v1     2026-07-14 09:36:50
25  note_structuring   claude-sonnet-5    aci-bench-heldout-v1     2026-08-31 20:54:49
```

Two problems a bare "latest 2 rows" query would hit:

1. **Coding's rows 3 and 4 are two ARMS of one benchmark** (P2-4), seconds
   apart, not two time windows. Taking the latest 2 would hand
   `compare_structuring_windows` a same-run arm comparison to treat as drift,
   which is nonsensical (coding has no paired accuracy metric at all; its
   numbers live in the `metrics` JSONB, not the accuracy family columns).
2. **`note_structuring`'s latest 2 rows by time are 25 and 8**, which are
   different datasets (ACI-Bench and PriMock57). Comparing across datasets is
   exactly what `compare_structuring_windows`'s `NOT_COMPARABLE` structural
   gate exists to refuse, so a naive query would manufacture a refusal instead
   of finding the real comparison, which is rows 7 and 25.

**The fix, used throughout this plan:** filter `eval_runs` to
`(agent_name, model)`, where `model` is the `model_inventory` row's own model,
before doing anything else. That alone solves problem 1: coding's
`model_inventory` row has `model='claude-opus-4-8'` (the routed arm, per
`shared.llm.ROUTING`), so the query returns only row 4, never row 3. For
problem 2, take the latest row, fix its `dataset_ref`, and filter to rows
sharing that dataset before taking the top 2. That returns exactly rows 25 and
7 for `note_structuring`, and correctly excludes row 8.

This does not change what the spec asks for: it changes how "the latest two
windows for an agent" is computed so it actually means what section 5 intends.

---

## File Structure

| File | Responsibility |
|---|---|
| `db/schema.sql` | **modify.** Four new `ALTER TABLE` lines for `model_inventory`. |
| `scripts/seed_model_inventory.py` | **create.** Upserts one row per clinical agent from committed language. |
| `governance/transparency.py` | **rewrite.** The live-join report builder. |
| `tests/test_transparency.py` | **create.** `needs_db`-guarded, against the real seeded rows and the real committed artifacts. |

---

## Chunk 1: schema and seed data

### Task 1: Migrate `model_inventory`

**Files:**
- Modify: `db/schema.sql` (after the existing `model_inventory` block, around line 84)

- [ ] **Step 1: Add the four columns**

```sql
-- P3-4: four more of HTI-1's nine source-attribute categories. Performance
-- and validation (the other three: details/output, quantitative performance,
-- and the update/revalidation schedule) are answered live from eval_runs and
-- governance/drift.py, never stored here, so they cannot go stale the way
-- governance/pricing.json and the P1-4 cache key both did.
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    cautioned_out_of_scope_use TEXT;
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    fairness_process_note TEXT;
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    external_validation_note TEXT;
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    maintenance_schedule TEXT;
```

- [ ] **Step 2: Apply it to the running database**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -c "from shared.db import get_conn; from pathlib import Path; conn = get_conn().__enter__(); conn.execute(Path('db/schema.sql').read_text(encoding='utf-8'))"`

Expected: no output, no error. `schema.sql` uses `IF NOT EXISTS` throughout, so
re-running it against a database that already has the older tables is safe.

- [ ] **Step 3: Verify the columns exist**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -c "from shared.db import get_conn; c = get_conn().__enter__(); print([r[0] for r in c.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='model_inventory'\").fetchall()])"`

Expected: a list including all 7 text columns:
`intended_use`, `training_data_note`, `known_limitations`,
`cautioned_out_of_scope_use`, `fairness_process_note`,
`external_validation_note`, `maintenance_schedule`.

- [ ] **Step 4: Commit**

```bash
git add db/schema.sql
git commit -m "feat(P3-4): four more model_inventory columns, named after the HTI-1 categories they answer"
```

### Task 2: Seed script

**Files:**
- Create: `scripts/seed_model_inventory.py`

- [ ] **Step 1: Write the script**

```python
"""Seed model_inventory with the 4 clinical agents (P3-4).

    python scripts/seed_model_inventory.py

Idempotent: upserts on (agent_name, model, version), the table's existing
unique constraint, so running this again after a routing change in
shared/llm.py ADDS a new row rather than mutating history, and old rows stay
queryable by whichever window they were current for.

model and version come from shared.llm.ROUTING, never hand-typed, so this
script cannot silently fall out of sync with what actually runs.

transparency and eval_judge are deliberately excluded: infrastructure with no
agent_decisions rows and no decision a clinician sees, not decision support
interventions in the HTI-1 sense.

Every string below traces to language this project has already committed and
vetted. Where a field has nothing truthful to say yet, it is None, with a
comment explaining why, not a plausible-sounding placeholder.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from governance.evaluate import UNSCOREABLE                      # noqa: E402
from services.agent_care_gap.rules import (                      # noqa: E402
    CITATIONS_VERIFIED_ON,
    RULES,
)
from shared.db import get_conn                                   # noqa: E402
from shared.llm import ROUTING                                   # noqa: E402

# P2-4, docs/ROADMAP.md: routed to claude-opus-4-8 at high ON COST, not a
# demonstrated quality win. Paired delta 0.70 points, 95% BCa CI
# [-0.73, 2.22], straddling zero.
_CODING_EXTERNAL_VALIDATION = (
    "Benchmarked against claude-sonnet-5 at xhigh on the ACI-Bench held-out "
    "set (P2-4, 2026-08-07): paired delta in not-found rate 0.70 points, 95% "
    "BCa CI [-0.73, 2.22]. The interval straddles zero, so this is NOT a "
    "demonstrated quality win over the alternative benchmarked; the routing "
    "decision was cost ($3.16 vs $4.01 per 120 notes), not accuracy.")

_STRUCTURING_TRAINING_NOTE = (
    "Scored asymmetrically against a clinician-written reference note: "
    "recall is scored against the note (did the model capture what the "
    "clinician wrote), precision against the transcript (is what the model "
    "wrote actually said). See governance.evaluate.score_structuring.")

_STRUCTURING_CAUTION = (
    "The PriMock57 held-out window (n=7, audio-sourced) is too small to "
    "quote as a headline beside ACI-Bench's n=120, and its accuracy is NULL "
    "by construction: placement is not scorable against an unsectioned GP "
    "note. See docs/ROADMAP.md P3-2.")

_care_gap_sources = "; ".join(
    f"{r.source.organization}, \"{r.source.title}\""
    f"{f' (grade {r.source.grade})' if r.source.grade else ''}, {r.source.year}"
    for r in RULES)

_ROWS = [
    dict(
        agent_name="note_structuring",
        intended_use=(
            "Structures a clinical encounter transcript or dictation into a "
            "four-section SOAP note for clinician review."),
        training_data_note=_STRUCTURING_TRAINING_NOTE,
        known_limitations=UNSCOREABLE.get(
            "note_structuring",
            "Recall and precision are measured against a held-out set; "
            "placement accuracy isolates structuring skill from capture "
            "skill. See governance.evaluate.score_structuring."),
        cautioned_out_of_scope_use=_STRUCTURING_CAUTION,
        fairness_process_note=None,  # no fairness process is defined for a
        # structuring task with no protected-class-conditioned outcome.
        external_validation_note=None,  # answered live: see Task 5.
        maintenance_schedule=None,  # no re-benchmark cadence is defined yet.
    ),
    dict(
        agent_name="coding",
        intended_use=(
            "Suggests ICD-10-CM and HCPCS Level II billing codes from a SOAP "
            "note, for human review before submission."),
        training_data_note=None,  # hosted model; no project-controlled
        # training data to disclose beyond the vendor's own model card.
        known_limitations=UNSCOREABLE["coding"],
        cautioned_out_of_scope_use=UNSCOREABLE["coding"],
        fairness_process_note=None,
        external_validation_note=_CODING_EXTERNAL_VALIDATION,
        maintenance_schedule=None,
    ),
    dict(
        agent_name="care_gap",
        intended_use=(
            "Flags candidate preventive-care gaps (screening, monitoring) "
            "from keyword matches in a SOAP note, for clinician review."),
        training_data_note=(
            "Deterministic rule matching, not a trained model. Haiku is used "
            "only to phrase the flagged gap in prose; it does not decide "
            "whether a gap fires."),
        known_limitations=UNSCOREABLE["care_gap"],
        cautioned_out_of_scope_use=(
            "A keyword match carries no age, interval, or already-done "
            "check. This matters most for LIPID_SCREENING, whose cited "
            "USPSTF grade B is scoped to adults 40-75 with at least one CVD "
            "risk factor and a calculated 10-year CVD risk of 10% or "
            "greater; a keyword scan cannot evaluate that threshold, so a "
            "fired gap does not mean the graded recommendation applies to "
            "this patient. Every gap is a candidate flag for clinician "
            "review, never a confirmed gap."),
        fairness_process_note=(
            f"Every rule traces to a graded published guideline, verified "
            f"{CITATIONS_VERIFIED_ON}: {_care_gap_sources}."),
        external_validation_note=None,
        maintenance_schedule=(
            f"Citations are point-in-time, verified {CITATIONS_VERIFIED_ON}, "
            f"against guidelines that are revised on their own schedule. No "
            f"automated re-verification exists yet."),
    ),
    dict(
        agent_name="prior_auth",
        intended_use=(
            "Flags SOAP note items that may require prior authorization, for "
            "clinician review."),
        training_data_note=None,
        known_limitations=UNSCOREABLE["prior_auth"],
        cautioned_out_of_scope_use=UNSCOREABLE["prior_auth"],
        fairness_process_note=None,
        external_validation_note=None,
        maintenance_schedule=None,
    ),
]


def seed() -> None:
    with get_conn() as conn:
        for row in _ROWS:
            model, effort = ROUTING[
                "structuring" if row["agent_name"] == "note_structuring"
                else row["agent_name"]]
            conn.execute(
                "INSERT INTO model_inventory "
                "(agent_name, model, version, intended_use, "
                " training_data_note, known_limitations, "
                " cautioned_out_of_scope_use, fairness_process_note, "
                " external_validation_note, maintenance_schedule) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (agent_name, model, version) DO UPDATE SET "
                "  intended_use = EXCLUDED.intended_use, "
                "  training_data_note = EXCLUDED.training_data_note, "
                "  known_limitations = EXCLUDED.known_limitations, "
                "  cautioned_out_of_scope_use = "
                "    EXCLUDED.cautioned_out_of_scope_use, "
                "  fairness_process_note = EXCLUDED.fairness_process_note, "
                "  external_validation_note = "
                "    EXCLUDED.external_validation_note, "
                "  maintenance_schedule = EXCLUDED.maintenance_schedule, "
                "  updated_at = now()",
                (row["agent_name"], model, model,  # version == model string;
                 # see the plan's note on why there is no separate semver
                 # for a hosted model in this project.
                 row["intended_use"], row["training_data_note"],
                 row["known_limitations"], row["cautioned_out_of_scope_use"],
                 row["fairness_process_note"],
                 row["external_validation_note"],
                 row["maintenance_schedule"]))
    print(f"seeded {len(_ROWS)} model_inventory rows")


if __name__ == "__main__":
    seed()
```

Note `version = model` (the hosted model string, e.g. `claude-opus-4-8`):
there is no separate semantic version for a hosted model in this project, and
the unique constraint is `(agent_name, model, version)`, so a distinct value
is still needed there rather than a constant that would collide across
agents sharing a model.

- [ ] **Step 2: Run it**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe scripts/seed_model_inventory.py`
Expected: `seeded 4 model_inventory rows`

- [ ] **Step 3: Verify content, not just row count**

Run:
```bash
PYTHONPATH="$PWD" .venv/Scripts/python.exe -c "
from shared.db import get_conn
with get_conn() as conn:
    rows = conn.execute('SELECT agent_name, model, version FROM model_inventory ORDER BY agent_name').fetchall()
    for r in rows: print(r)
"
```
Expected: 4 rows, `('care_gap', 'claude-haiku-4-5-20251001', 'claude-haiku-4-5-20251001')`, `('coding', 'claude-opus-4-8', 'claude-opus-4-8')`, `('note_structuring', 'claude-sonnet-5', 'claude-sonnet-5')`, `('prior_auth', 'claude-sonnet-5', 'claude-sonnet-5')`.

- [ ] **Step 4: Verify idempotence**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe scripts/seed_model_inventory.py` a second time, then re-run Step 3's query.
Expected: still exactly 4 rows (the `ON CONFLICT` updated them, not duplicated them).

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add scripts/seed_model_inventory.py
git commit -m "feat(P3-4): seed model_inventory from language this project already vetted"
```

---

## Chunk 2: the live join and the report

### Task 3: `governance/transparency.py`, no-history case

**Files:**
- Modify: `governance/transparency.py` (full rewrite)
- Test: `tests/test_transparency.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""P3-4: the transparency report, joined live against real eval_runs data.

Guarded by needs_db, mirroring tests/test_registry.py: local dev has no
standing Postgres unless it was started for this session; CI's postgres:16
service always does.

Assumes scripts/seed_model_inventory.py has already been run against this
database (Task 2). This module reads what that script wrote; it does not
re-seed, so a test failure here after a ROUTING change means re-run the seed
script, not a bug in this test.
"""
from __future__ import annotations

import psycopg
import pytest

from shared.config import settings
from governance.transparency import HTI1_CATEGORIES, build_report


def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="no reachable Postgres")


@needs_db
def test_report_has_one_row_per_seeded_agent():
    rows = build_report()
    names = {r["agent_name"] for r in rows}
    assert names == {"note_structuring", "coding", "care_gap", "prior_auth"}


@needs_db
def test_every_row_carries_all_nine_categories():
    """Every category is present, even where the honest value is None."""
    for row in build_report():
        missing = [c for c in HTI1_CATEGORIES if c not in row]
        assert not missing, f"{row['agent_name']} is missing {missing}"


@needs_db
def test_codings_cautioned_use_is_the_actual_unscoreable_string():
    """Not a paraphrase: the same string governance.evaluate already asserts.

    If someone edits UNSCOREABLE["coding"] without touching this report, this
    test catches the drift, which is the whole point of sourcing from it
    rather than retyping it.
    """
    from governance.evaluate import UNSCOREABLE

    row = next(r for r in build_report() if r["agent_name"] == "coding")
    assert row["Cautioned out-of-scope use of the intervention"] == (
        UNSCOREABLE["coding"])
```

- [ ] **Step 2: Run it to confirm the shape is wrong**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_transparency.py -v`
Expected: FAIL. `ImportError: cannot import name 'HTI1_CATEGORIES'` (the stub
has no such name, and its `build_report` returns 6-field dicts, not 4 rows
with 9 categories).

- [ ] **Step 3: Write the category names and the row skeleton**

```python
"""P3-4: an ONC HTI-1 style transparency report over the model registry.

The 9 category names below are the source-attribute categories AHIMA's
summary of the HTI-1 final rule groups its 31 required attributes into
(the Federal Register page itself blocks automated fetching; this is the
best available primary-adjacent source, and this report claims HTI-1
"style," not certification). Six are answered from model_inventory,
seeded by scripts/seed_model_inventory.py from language this project has
already committed elsewhere. Three (details/output, quantitative
performance, and the update/revalidation schedule) are answered LIVE from
eval_runs and governance.drift, never stored as text: a stored number
goes stale the moment a new window is filed, the way
governance/pricing.json and the P1-4 cache key both did, silently.

build_report() takes no arguments. It reads whatever the database
currently holds, so it is only as current as the last seed and the last
eval_runs write, and never any less current than that.
"""
from __future__ import annotations

import json
from pathlib import Path

from governance.drift import DriftVerdict, compare_structuring_windows
from shared.db import get_conn

ARTIFACT_DIR = (Path(__file__).resolve().parent / "eval_artifacts")

# Order matches the AHIMA summary of the HTI-1 source-attribute categories.
HTI1_CATEGORIES = (
    "Details and output of the DSI",
    "Purpose of the intervention",
    "Cautioned out-of-scope use of the intervention",
    "Intervention development details and input features",
    "Process used to ensure fairness in development of the intervention",
    "External validation process",
    "Quantitative measures of performance",
    "Ongoing maintenance of intervention implementation and use",
    "Updates and continued validation or fairness assessment schedule",
)

_INVENTORY_COLUMNS = (
    "agent_name", "model", "version", "intended_use", "training_data_note",
    "known_limitations", "cautioned_out_of_scope_use",
    "fairness_process_note", "external_validation_note",
    "maintenance_schedule",
)

# model_inventory column -> HTI-1 category it answers.
_STATIC_MAPPING = {
    "intended_use": "Purpose of the intervention",
    "training_data_note": "Intervention development details and input features",
    "known_limitations": "Cautioned out-of-scope use of the intervention",
    "cautioned_out_of_scope_use": "Cautioned out-of-scope use of the intervention",
    "fairness_process_note": "Process used to ensure fairness in development of the intervention",
    "external_validation_note": "External validation process",
    "maintenance_schedule": "Ongoing maintenance of intervention implementation and use",
}


def _inventory_rows() -> list[dict]:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT agent_name, model, version, intended_use, "
            "training_data_note, known_limitations, "
            "cautioned_out_of_scope_use, fairness_process_note, "
            "external_validation_note, maintenance_schedule "
            "FROM model_inventory ORDER BY agent_name")
        return [dict(zip(_INVENTORY_COLUMNS, row)) for row in cur.fetchall()]


def build_report() -> list[dict]:
    return [_report_row(inv) for inv in _inventory_rows()]


def _report_row(inv: dict) -> dict:
    row = {"agent_name": inv["agent_name"], "model": inv["model"]}

    for column, category in _STATIC_MAPPING.items():
        # known_limitations and cautioned_out_of_scope_use both target the
        # same category for agents where they read the same (coding,
        # prior_auth); the more specific one, written second, wins.
        row[category] = inv[column]

    row.update(_performance_and_validation(inv["agent_name"], inv["model"]))
    return row
```

- [ ] **Step 4: Stub `_performance_and_validation` to make the shape tests pass**

```python
def _performance_and_validation(agent_name: str, model: str) -> dict:
    return {
        "Details and output of the DSI": "not yet measured",
        "Quantitative measures of performance": "not yet measured",
        "Updates and continued validation or fairness assessment schedule":
            "not yet measured",
    }
```

- [ ] **Step 5: Run the three tests from Step 1**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_transparency.py -v`
Expected: all 3 PASS. (`"not yet measured"` everywhere is honest but not yet
useful; Task 4 replaces the stub with the real query.)

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add governance/transparency.py tests/test_transparency.py
git commit -m "feat(P3-4): the 9-category report shape, performance stubbed"
```

### Task 4: the live join, no drift yet

**Files:**
- Modify: `governance/transparency.py`
- Test: `tests/test_transparency.py`

- [ ] **Step 1: Write the failing tests**

```python
@needs_db
def test_an_agent_with_no_eval_runs_reports_not_yet_measured():
    """prior_auth has no held-out set at all, so no eval_runs row exists."""
    row = next(r for r in build_report() if r["agent_name"] == "prior_auth")
    assert row["Quantitative measures of performance"] == "not yet measured"


@needs_db
def test_codings_performance_comes_from_its_own_arms_metrics_jsonb():
    """coding has 2 eval_runs rows (P2-4's two arms), but only ONE of them
    has model='claude-opus-4-8', the routed arm model_inventory records.
    Filtering by model, not just by recency, is what keeps the OTHER arm
    (claude-sonnet-5) out of this report row.
    """
    row = next(r for r in build_report() if r["agent_name"] == "coding")
    perf = row["Quantitative measures of performance"]
    assert "verified_rate" in perf
    assert "sonnet" not in perf.lower()
```

- [ ] **Step 2: Run them to confirm failure**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_transparency.py -k "not_yet_measured or codings_performance" -v`
Expected: `test_codings_performance_comes_from_its_own_arms_metrics_jsonb` FAILS
(`perf == "not yet measured"`, no `verified_rate` in it).
`test_an_agent_with_no_eval_runs_reports_not_yet_measured` already PASSES from
the Task 3 stub; that is fine, it stays green through this step.

- [ ] **Step 3: Implement the real join**

```python
def _same_model_windows(agent_name: str, model: str) -> list[dict]:
    """Every eval_runs row for THIS agent's THIS model, newest first.

    Filtering by model is what keeps coding's two P2-4 arms from being read
    as two time windows of one thing: model_inventory's coding row has
    model='claude-opus-4-8' (the routed arm), so this returns only that arm's
    row, never claude-sonnet-5's.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, window_label, dataset_ref, n_examples, accuracy, f1, "
            "precision, recall, metrics, created_at FROM eval_runs "
            "WHERE agent_name = %s AND model = %s ORDER BY created_at DESC",
            (agent_name, model))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _quantitative_summary(row: dict) -> str:
    """One line naming the real numbers this row measured.

    accuracy/f1/precision/recall are the paired-comparable family (P3-1); a
    row that has none of those (coding, care_gap, prior_auth: all NULL by
    the P3-1 guard) falls back to the metrics JSONB's own headline number,
    named explicitly rather than printed as a bare float.
    """
    parts = [f"{name}={row[name]:.6f}" for name in
             ("accuracy", "f1", "precision", "recall") if row[name] is not None]
    if parts:
        return f"window {row['window_label']!r}, n={row['n_examples']}: " + \
               ", ".join(parts)

    metrics = row["metrics"] or {}
    if "verified_rate" in metrics:
        return (f"window {row['window_label']!r}, n={row['n_examples']}: "
                f"verified_rate={metrics['verified_rate']:.2f}, "
                f"not_found_rate={metrics.get('not_found_rate', 'n/a')}")
    return f"window {row['window_label']!r}, n={row['n_examples']}: no scored metric"


def _performance_and_validation(agent_name: str, model: str) -> dict:
    windows = _same_model_windows(agent_name, model)
    if not windows:
        return {
            "Details and output of the DSI": "not yet measured",
            "Quantitative measures of performance": "not yet measured",
            "Updates and continued validation or fairness assessment schedule":
                "not yet measured",
        }

    latest = windows[0]
    same_dataset = [w for w in windows if w["dataset_ref"] == latest["dataset_ref"]][:2]

    details = (f"{agent_name} on {model}, dataset {latest['dataset_ref']!r}, "
              f"measured {latest['created_at'].date().isoformat()}")
    performance = _quantitative_summary(latest)

    if len(same_dataset) < 2:
        validation = ("only one window filed for this dataset; no drift "
                      "comparison possible yet")
    elif agent_name != "note_structuring":
        # No paired-comparable metric exists for this agent (P3-1's
        # accuracy-family guard). Two same-model rows here would be unusual
        # today (coding's model filter already isolates one arm), but if it
        # ever happens, state that plainly rather than attempting a
        # comparison compare_structuring_windows was not built to make.
        validation = (f"{len(same_dataset)} windows filed, but "
                      f"{agent_name!r} has no paired-comparable accuracy "
                      f"metric to compare across them")
    else:
        validation = _drift_summary(same_dataset)

    return {
        "Details and output of the DSI": details,
        "Quantitative measures of performance": performance,
        "Updates and continued validation or fairness assessment schedule":
            validation,
    }
```

- [ ] **Step 4: Run all transparency tests**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_transparency.py -v`
Expected: all PASS except any that reference `_drift_summary`, which does not
exist yet. If the test file from Step 1 has no such reference, all 5 tests so
far PASS.

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add governance/transparency.py tests/test_transparency.py
git commit -m "feat(P3-4): live performance join, filtered by the row's own model"
```

### Task 5: the drift comparison for `note_structuring`

**Files:**
- Modify: `governance/transparency.py`
- Test: `tests/test_transparency.py`

- [ ] **Step 1: Write the failing test**

```python
@needs_db
def test_note_structurings_two_aci_windows_produce_the_p3_3_verdict():
    """Windows 7 and 25. Same verdict scripts/run_drift_check.py reports for
    the same two artifacts: NOT_ATTRIBUTABLE, naming max_tokens.
    """
    row = next(r for r in build_report() if r["agent_name"] == "note_structuring")
    validation = row[
        "Updates and continued validation or fairness assessment schedule"]
    assert "NOT_ATTRIBUTABLE" in validation
    assert "max_tokens" in validation


@needs_db
def test_the_report_never_crashes_on_a_null_max_tokens_side():
    """Smoke test: build_report() runs to completion against the real,
    currently-committed data, top to bottom, for every agent."""
    rows = build_report()
    assert len(rows) == 4
    for row in rows:
        for category in HTI1_CATEGORIES:
            assert category in row
```

- [ ] **Step 2: Run to confirm failure**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_transparency.py -k p3_3_verdict -v`
Expected: FAIL, `NameError: name '_drift_summary' is not defined`.

- [ ] **Step 3: Implement `_drift_summary`**

```python
def _artifact_payload(row: dict) -> dict:
    name = (row["metrics"] or {}).get("provenance", {}).get("artifact")
    if not name:
        raise ValueError(
            f"eval_runs row {row['id']} has no provenance.artifact; it was "
            f"filed before P3-1 started recording it, or by a path that "
            f"never wrote one. Re-file it with scripts/refile_eval_run.py "
            f"before it can be drift-compared.")
    return json.loads((ARTIFACT_DIR / name).read_text(encoding="utf-8"))


def _drift_summary(same_dataset: list[dict]) -> str:
    """same_dataset[0] is NEWEST (query is ORDER BY created_at DESC)."""
    newer, older = same_dataset[0], same_dataset[1]
    result = compare_structuring_windows(
        _artifact_payload(older), _artifact_payload(newer), replicates=2000)

    header = (f"{result.verdict.value.upper()}: {older['window_label']!r} "
             f"({older['created_at'].date().isoformat()}) vs "
             f"{newer['window_label']!r} "
             f"({newer['created_at'].date().isoformat()}), delta "
             f"{result.delta:+.6f}" if result.delta is not None else
             f"{result.verdict.value.upper()}")

    if result.verdict is DriftVerdict.NOT_ATTRIBUTABLE:
        return header + ". " + " ".join(result.caveats)
    return header
```

Wire it in: replace the `else: validation = _drift_summary(same_dataset)`
branch already written in Task 4 (it already calls this function; Task 4 left
it undefined on purpose so the test in this task is the one that exercises
it).

- [ ] **Step 4: Run all transparency tests**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/test_transparency.py -v`
Expected: all PASS, including the smoke test that walks every category for
every one of the 4 real seeded agents against the real, currently-committed
database.

- [ ] **Step 5: Full suite and lint**

Run: `PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest -q`
Expected: prior count plus the transparency tests, all green, 0 xfailed
(P3-3 already cleared the one that used to be here).

Run: `make lint`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add governance/transparency.py tests/test_transparency.py
git commit -m "feat(P3-4): drift verdict as the ongoing-validation category for note_structuring"
```

### Task 6: roadmap evidence

**Files:**
- Modify: `docs/ROADMAP.md` (the P3-4 line)

- [ ] **Step 1: Run the report and capture real output**

```bash
PYTHONPATH="$PWD" .venv/Scripts/python.exe -c "
import json
from governance.transparency import build_report
for row in build_report():
    print(row['agent_name'], '->')
    for k, v in row.items():
        if k in ('agent_name',): continue
        print(f'  {k}: {v}')
"
```

- [ ] **Step 2: Write the DONE entry**

Follow the P3-1/P3-2/P3-3 format: state the gate, paste the real per-agent
output from Step 1 (or a representative excerpt for `note_structuring` and
`coding`, the two most informative rows), and state plainly that
`note_structuring`'s validation category reads `NOT_ATTRIBUTABLE` for the
same three reasons P3-3 found, since that is the honest current state and
the entry should say so rather than let a reader assume the report shows a
clean pass.

- [ ] **Step 3: Commit, push, open the PR**

```bash
git add docs/ROADMAP.md
git commit -m "docs(P3-4): gate evidence"
git push -u origin p3-4-transparency-report
gh pr create --base main --title "P3-4: transparency report, joined live so it can't quote a stale number"
```

---

## Verification checklist

- [ ] `model_inventory` has 4 rows, one per clinical agent, `model`/`version` sourced from `ROUTING`
- [ ] Every report row carries all 9 HTI-1 category names
- [ ] `coding`'s cautioned-use text is byte-identical to `UNSCOREABLE["coding"]`
- [ ] `coding`'s performance category never contains the other arm's model name
- [ ] `note_structuring`'s validation category reproduces P3-3's `NOT_ATTRIBUTABLE` verdict and caveats on the real windows
- [ ] An agent with zero `eval_runs` rows reports `"not yet measured"`, not a crash or a missing key
- [ ] Re-running the seed script does not duplicate rows
- [ ] Full suite and `make lint` clean
