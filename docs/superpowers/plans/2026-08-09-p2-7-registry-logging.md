# P2-7: Registry Logging Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `shared/registry.py::decisions_for_encounter`'s incomplete
`SELECT`, expose it as a tested `GET /encounters/{id}/decisions` endpoint on
the orchestrator, and document the one intentional NULL (`care_gap`'s
`effort`) so the roadmap gate's "every decision, all six fields, queryable by
encounter id" is proved rather than assumed.

**Architecture:** No new service, no schema migration. `decisions_for_encounter`
widens from a 5-column to an 8-column `SELECT` (the row shape does not
change; the query was just incomplete). A `DecisionRecord` pydantic model in
`shared/schemas.py` gives that row a typed contract for the new endpoint.
`log_decision`, every agent's write path, and `agent_decisions` itself are
already correct and untouched.

**Tech Stack:** Python 3.12 (CI, image) / 3.10.11 (local venv), FastAPI,
psycopg 3.2.3, pydantic 2.10.4, pytest.

**Spec:** `docs/superpowers/specs/2026-08-09-p2-7-registry-logging-design.md`

**Model/effort:** per `docs/MODEL-EFFORT-GUIDE.md` line 68, P2-7 recommends
Sonnet 5 at `high` ("audit correctness matters"). Confirm the session matches
before starting.

**Branch:** `p2-7-registry-logging`, already created, already carries the
spec commit. Built on top of the still-unmerged P2-6 and Postgres-PVC
branches (PRs #5 and #6); rebase onto `main` once those land, per Task 7.

---

## Read this before Task 1

Two things here are easy to get subtly wrong.

**1. `decisions_for_encounter`'s bug is a dropped `SELECT` column, not a
missing write.** Every field already lands in `agent_decisions` correctly.
Do not "fix" this by touching `log_decision` or the schema; the fix is
entirely in the query's column list.

**2. `care_gap`'s `effort=None` stays `None`.** Do not change it to a
sentinel string while touching that file. The point of Task 6 is a comment
explaining why the NULL is correct, and a test asserting it reads back as
`None`, not filling it in.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `shared/registry.py` | write and read the audit trail | widen the `SELECT` |
| `shared/schemas.py` | one place for data shapes | add `DecisionRecord` |
| `services/orchestrator/app.py` | HTTP surface | add `GET /encounters/{id}/decisions` |
| `services/agent_care_gap/app.py` | care-gap endpoint | comment only |
| `tests/test_registry.py` | registry against a real database | new |
| `tests/test_orchestrator_integration.py` | orchestrator HTTP surface | two new tests |
| `docs/ROADMAP.md` | P2-7 evidence entry | append |

---

## Chunk 1: Fix the query, add the schema

### Task 1: Widen `decisions_for_encounter`'s SELECT

**Files:**
- Modify: `shared/registry.py`
- Test: `tests/test_registry.py` (created here)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_registry.py`:

```python
"""P2-7: shared/registry.py against a real database.

Guarded by needs_db, mirroring tests/test_heldout_guard.py's needs_data
pattern: local dev has no standing Postgres (see the env-quirks project
note), CI's postgres:16 service always does.

log_decision and decisions_for_encounter are exercised against a real
Postgres rather than mocked, because the bug this task fixes (a SELECT
silently dropping columns) is exactly the class of bug a mocked connection
cannot catch.
"""
from __future__ import annotations

import psycopg
import pytest

from shared.config import settings
from shared.db import get_conn, insert_encounter, insert_note
from shared.registry import decisions_for_encounter, log_decision


def _db_reachable() -> bool:
    """A bounded timeout matters here specifically: an unreachable host must
    fail fast rather than hang collection on the OS's default TCP timeout."""
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(),
                              reason="no reachable Postgres")


@pytest.fixture
def encounter_and_note():
    encounter_id = insert_encounter("p2-7-test", "transcript")
    note_id = insert_note(encounter_id, {"subjective": "s", "objective": "o",
                                         "assessment": "a", "plan": "p"},
                          "test-model", None)
    yield encounter_id, note_id
    with get_conn() as conn:
        conn.execute("DELETE FROM agent_decisions WHERE encounter_id = %s",
                     (encounter_id,))
        conn.execute("DELETE FROM notes WHERE id = %s", (note_id,))
        conn.execute("DELETE FROM encounters WHERE id = %s", (encounter_id,))


@needs_db
def test_a_logged_decision_round_trips_every_field(encounter_and_note):
    encounter_id, note_id = encounter_and_note
    log_decision(
        encounter_id=encounter_id, note_id=note_id, agent_name="prior_auth",
        model="claude-sonnet-5", effort="high",
        input_ref={"subjective": "s"}, output={"items": []},
        confidence=0.75, latency_ms=4284,
    )

    rows = decisions_for_encounter(encounter_id)

    assert len(rows) == 1
    row = rows[0]
    assert row["agent_name"] == "prior_auth"
    assert row["note_id"] == note_id
    assert row["model"] == "claude-sonnet-5"
    assert row["model_effort"] == "high"
    assert row["input_ref"] == {"subjective": "s"}
    assert row["output"] == {"items": []}
    assert row["confidence"] == 0.75
    assert row["latency_ms"] == 4284
    assert row["created_at"] is not None


@needs_db
def test_the_query_returns_every_decision_for_the_encounter_in_order(
        encounter_and_note):
    encounter_id, note_id = encounter_and_note
    for agent, model in (("care_gap", "rules-v1"),
                         ("prior_auth", "claude-sonnet-5"),
                         ("coding", "claude-opus-4-8")):
        log_decision(encounter_id=encounter_id, note_id=note_id,
                    agent_name=agent, model=model, effort=None,
                    input_ref={}, output={}, confidence=0.5, latency_ms=1)

    rows = decisions_for_encounter(encounter_id)

    assert [r["agent_name"] for r in rows] == ["care_gap", "prior_auth",
                                               "coding"]


@needs_db
def test_an_encounter_with_no_decisions_returns_an_empty_list(
        encounter_and_note):
    encounter_id, _ = encounter_and_note
    assert decisions_for_encounter(encounter_id) == []
```

- [ ] **Step 2: Confirm a database is reachable, then run and watch it fail**

This task needs a live Postgres. If you have the kind cluster from P2-6 up:

```bash
kubectl port-forward -n care-ops svc/db 5432:5432 &
```

Otherwise `docker compose up db` per `CLAUDE.md` setup. Then:

```bash
.venv/Scripts/python.exe -m pytest tests/test_registry.py -q -p no:warnings
```

Expected: `test_a_logged_decision_round_trips_every_field` FAILs with
`KeyError: 'model_effort'` (the current `SELECT` does not return that
column). If instead every test is skipped, the database is not reachable;
fix that before continuing, since the whole point of this suite is testing
against a real one.

- [ ] **Step 3: Widen the SELECT**

In `shared/registry.py`, replace `decisions_for_encounter`:

```python
def decisions_for_encounter(encounter_id: int) -> list[dict]:
    """Every field required by the P2-7 gate (input, output, confidence,
    model, effort, latency), plus note_id: it is already stored on every
    row and is the join key back to `notes`, so leaving it out would make
    this a half-finished audit trail."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT agent_name, note_id, model, model_effort, input_ref, "
            "       output, confidence, latency_ms, created_at "
            "FROM agent_decisions WHERE encounter_id = %s ORDER BY created_at",
            (encounter_id,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
```

- [ ] **Step 4: Run and watch it pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_registry.py -q -p no:warnings
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add shared/registry.py tests/test_registry.py
git commit -m "fix(P2-7): decisions_for_encounter was dropping model_effort, input_ref, latency_ms"
```

---

### Task 2: Confirm the intentional NULL is tested

**Files:**
- Modify: `tests/test_registry.py`

`care_gap` passing `effort=None` is the one deliberate gap the schema
carries. Task 6 adds the code comment explaining it; this step adds the test
that keeps it honest, now while the query fix is fresh.

- [ ] **Step 1: Add the test**

Append to `tests/test_registry.py`:

```python
@needs_db
def test_a_rules_engine_decision_reads_back_effort_as_none(encounter_and_note):
    """The one intentional NULL in the schema: care_gap has no effort
    level, and this must stay NULL rather than drift to a sentinel string.
    See services/agent_care_gap/app.py for why."""
    encounter_id, note_id = encounter_and_note
    log_decision(
        encounter_id=encounter_id, note_id=note_id, agent_name="care_gap",
        model="rules-v1", effort=None,
        input_ref={}, output={"gaps": []}, confidence=0.9, latency_ms=1,
    )

    row = decisions_for_encounter(encounter_id)[0]
    assert row["model_effort"] is None
```

- [ ] **Step 2: Run and watch it pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_registry.py -q -p no:warnings
```

Expected: `4 passed`. This already passes with no code change, since
`log_decision` and the schema were already correct; the test exists to
prevent regression, not to fix anything.

- [ ] **Step 3: Commit**

```bash
git commit -am "test(P2-7): lock in care_gap's NULL effort as intentional"
```

---

### Task 3: `DecisionRecord` schema

**Files:**
- Modify: `shared/schemas.py`

- [ ] **Step 1: Add the import and the model**

`shared/schemas.py` does not import `datetime` yet. Add it alongside the
existing `typing` import:

```python
from datetime import datetime
from typing import Literal
```

Then add, near the bottom of the file, after `PipelineResult`:

```python
# ---------- Layer 3: audit trail read path ----------

class DecisionRecord(BaseModel):
    """One row from agent_decisions, as returned by a query-by-encounter
    lookup (shared/registry.py::decisions_for_encounter). Not the same
    shape as PriorAuthOutput etc: this is the audit envelope around a
    decision, not the decision's own artifact schema."""
    agent_name: str
    note_id: int
    model: str
    model_effort: str | None
    input_ref: dict
    output: dict
    confidence: float
    latency_ms: int | None
    created_at: datetime
```

- [ ] **Step 2: Confirm it imports and round-trips against a real row**

```bash
.venv/Scripts/python.exe -c "
from shared.schemas import DecisionRecord
from shared.registry import decisions_for_encounter, log_decision
from shared.db import insert_encounter, insert_note, get_conn

eid = insert_encounter('p2-7-schema-check', 'transcript')
nid = insert_note(eid, {'subjective':'s','objective':'o','assessment':'a','plan':'p'}, 'x', None)
log_decision(encounter_id=eid, note_id=nid, agent_name='prior_auth',
            model='claude-sonnet-5', effort='high', input_ref={'a':1},
            output={'b':2}, confidence=0.5, latency_ms=10)
rows = decisions_for_encounter(eid)
rec = DecisionRecord(**rows[0])
print(rec)
with get_conn() as conn:
    conn.execute('DELETE FROM agent_decisions WHERE encounter_id=%s', (eid,))
    conn.execute('DELETE FROM notes WHERE id=%s', (nid,))
    conn.execute('DELETE FROM encounters WHERE id=%s', (eid,))
print('cleaned up')
"
```

Expected: a printed `DecisionRecord(...)` and `cleaned up`, no validation
error. Requires the same reachable database as Task 1.

- [ ] **Step 3: Run the whole suite to confirm nothing else broke**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:warnings
```

Expected: same pass count as before, plus the 4 new `test_registry.py`
passes.

- [ ] **Step 4: Commit**

```bash
git add shared/schemas.py
git commit -m "feat(P2-7): add DecisionRecord, the audit-row read contract"
```

---

## Chunk 2: The endpoint

### Task 4: `GET /encounters/{id}/decisions`

**Files:**
- Modify: `services/orchestrator/app.py`
- Test: `tests/test_orchestrator_integration.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_integration.py`:

```python
# ---------- P2-7: the audit-trail read endpoint ----------
#
# decisions_for_encounter is monkeypatched here; the real query is already
# proven against a real database in tests/test_registry.py. This is about
# the HTTP surface: status code, response shape, the empty-list case.

def test_get_decisions_returns_the_persisted_rows(client, monkeypatch):
    from datetime import datetime, timezone
    stub_rows = [{
        "agent_name": "prior_auth", "note_id": 1, "model": "claude-sonnet-5",
        "model_effort": "high", "input_ref": {"subjective": "s"},
        "output": {"items": []}, "confidence": 0.75, "latency_ms": 4284,
        "created_at": datetime.now(timezone.utc),
    }]
    monkeypatch.setattr("services.orchestrator.app.decisions_for_encounter",
                        lambda encounter_id: stub_rows)

    resp = client.get("/encounters/1/decisions")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["agent_name"] == "prior_auth"
    assert body[0]["model_effort"] == "high"


def test_get_decisions_for_an_unknown_encounter_is_an_empty_list_not_404(
        client, monkeypatch):
    """No existence check against `encounters`: an id with no decisions
    yet and an id that was never created both return [] with 200. A
    stated simplification for a debugging endpoint, not an oversight."""
    monkeypatch.setattr("services.orchestrator.app.decisions_for_encounter",
                        lambda encounter_id: [])
    resp = client.get("/encounters/999999/decisions")
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run and watch it fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_orchestrator_integration.py -k decisions -q -p no:warnings
```

Expected: both tests FAIL, and before either request runs: `monkeypatch.setattr`
raises `AttributeError` because `services.orchestrator.app` has not imported
`decisions_for_encounter` yet. That is the correct failure for this step;
Step 3 makes it importable and adds the route in the same change.

- [ ] **Step 3: Add the endpoint**

In `services/orchestrator/app.py`:

```python
from shared.schemas import AgentInput, DecisionRecord, PipelineResult
from shared.registry import decisions_for_encounter
from services.orchestrator.graph import run_agents
```

Then, after the `/run` endpoint:

```python
@app.get("/encounters/{encounter_id}/decisions",
        response_model=list[DecisionRecord])
def get_decisions(encounter_id: int):
    # Synchronous, not async: one blocking psycopg call, no fan-out, unlike
    # /run. No existence check against `encounters`: an id with no
    # decisions yet and an id that was never created both legitimately
    # return []. See spec section 3 for why that is a stated
    # simplification rather than an oversight.
    return decisions_for_encounter(encounter_id)
```

- [ ] **Step 4: Run and watch it pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_orchestrator_integration.py -k decisions -q -p no:warnings
```

Expected: `2 passed`

- [ ] **Step 5: Lint**

```bash
.venv/Scripts/python.exe -m ruff check services/orchestrator/
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add services/orchestrator/app.py tests/test_orchestrator_integration.py
git commit -m "feat(P2-7): GET /encounters/{id}/decisions on the orchestrator"
```

---

### Task 5: Full endpoint test suite still green

**Files:** none, verification only

- [ ] **Step 1: Full orchestrator integration file**

```bash
.venv/Scripts/python.exe -m pytest tests/test_orchestrator_integration.py -q -p no:warnings
```

Expected: `9 passed` (the 7 from P2-6 plus the 2 new ones).

- [ ] **Step 2: Whole suite**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:warnings
```

Expected: every test passing, no unexpected skips beyond `needs_data` and
(if no database is reachable in this environment) `needs_db`.

---

## Chunk 3: The documented NULL, verification, and evidence

### Task 6: Comment the intentional NULL

**Files:**
- Modify: `services/agent_care_gap/app.py`

- [ ] **Step 1: Add the comment**

Find the `log_decision` call in `services/agent_care_gap/app.py` (currently
around line 33) and change:

```python
        agent_name="care_gap", model="rules-v1", effort=None,
```

to:

```python
        agent_name="care_gap", model="rules-v1",
        effort=None,  # deterministic rules engine, not an LLM call: no
                      # effort level exists to record. NULL is the honest
                      # value here, not a fabricated "n/a" string. Locked
                      # in by tests/test_registry.py::
                      # test_a_rules_engine_decision_reads_back_effort_as_none
```

- [ ] **Step 2: Run the care-gap tests to confirm no behavior changed**

```bash
.venv/Scripts/python.exe -m pytest tests/test_care_gap_app.py tests/test_care_gap_rules.py -q -p no:warnings
```

Expected: same pass count as before this task; this is a comment-only
change.

- [ ] **Step 3: Commit**

```bash
git commit -am "docs(P2-7): explain why care_gap's effort is NULL, not fabricated"
```

---

### Task 7: Rebase onto main, once P2-6 and the PVC fix are merged

**This step needs the human first.** PRs #5 (P2-6) and #6 (Postgres PVC) must
be merged before this branch can be based on a clean `main`.

- [ ] **Step 1: Confirm both are merged**

```bash
gh pr view 5 --json state,mergedAt
gh pr view 6 --json state,mergedAt
```

Expected: both `"state": "MERGED"`.

- [ ] **Step 2: Rebase**

```bash
git fetch origin
git rebase origin/main
```

Resolve any conflicts (expected to be none: this branch only touches
`shared/registry.py`, `shared/schemas.py`,
`services/orchestrator/app.py`, `services/agent_care_gap/app.py`, and new
test files, none of which P2-6 or the PVC fix touched).

- [ ] **Step 3: Re-run the whole suite post-rebase**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:warnings
.venv/Scripts/python.exe -m ruff check .
```

Expected: all green, same as before the rebase.

---

### Task 8: Live in-cluster verification (needs a human)

**Cannot run unattended.** Needs the kind cluster up (it already is, from
P2-6) and reuses the encounter seeded there, now on the PVC-backed database
so it persists.

- [ ] **Step 1: Rebuild and reload the orchestrator image**

Required: the new endpoint does not exist in the currently-deployed image.

```bash
docker build -f services/orchestrator/Dockerfile -t care-ops-orchestrator:latest .
~/bin/kind load docker-image care-ops-orchestrator:latest --name care-ops
kubectl rollout restart deployment/orchestrator -n care-ops
kubectl rollout status deployment/orchestrator -n care-ops --timeout=120s
```

- [ ] **Step 2: Confirm the seeded encounter is still there**

The P2-6 encounter (id 1) was on the pre-PVC database and is gone; the PVC
fix's verification also used and cleaned up its own sentinel. Check and
reseed if needed:

```bash
kubectl exec -n care-ops deploy/postgres -- psql -U care_ops -d care_ops -c "SELECT id FROM encounters;"
```

If empty, reseed per P2-6's Task 12 steps 3, using external_ref
`p2-7-verify`.

- [ ] **Step 3: Run the pipeline, then query the decisions**

```bash
kubectl port-forward -n care-ops svc/orchestrator 8080:8000 &
curl -s -X POST http://localhost:8080/run -H 'Content-Type: application/json' \
  -d '{"encounter_id": <ENC>, "note_id": <NOTE>, "soap": {"subjective":"Patient has diabetes and reports low back pain for six weeks.","objective":"BP 138/86.","assessment":"Type 2 diabetes. Lumbar radiculopathy.","plan":"Order MRI lumbar spine. Check A1c."}}' \
  -o /tmp/p2-7-run.json -w "HTTP %{http_code}\n"

curl -s http://localhost:8080/encounters/<ENC>/decisions | tee /tmp/p2-7-decisions.json
```

Expected: the `/run` response has `errors: {}` and three artifacts. The
`/decisions` response is a JSON array of exactly 3 objects, one per
`agent_name` (`prior_auth`, `care_gap`, `coding`), each with a real `model`,
and the `care_gap` entry has `"model_effort": null`.

- [ ] **Step 4: Record the evidence in `docs/ROADMAP.md`**

Append a `**DONE 2026-08-XX.**` block under P2-7, in the P2-6/P2-4 style:
what ran, the exact observed `/decisions` response, and confirmation that
`decisions_for_encounter`'s fix and `care_gap`'s NULL are both visible in
the live response, not only in pytest.

---

### Task 9: Finish the branch

- [ ] **Step 1: Update `docs/TECH-DESIGN.md` if it now reads incompletely**

Section 3.2 describes the orchestrator's `/run` contract but not
`/encounters/{id}/decisions`. Add a short entry for it, in the same
request/response JSON style as the rest of section 3.

- [ ] **Step 2: Use superpowers:finishing-a-development-branch**

Open the PR with the measured evidence in the body: the query-fix diff, the
new test counts, and the live `/decisions` response.

- [ ] **Step 3: State the phase gate**

Per `CLAUDE.md`, P2-7 completing the roadmap task also completes Phase 2's
exit gate: "a note submitted to the orchestrator returns all three
structured artifacts, each logged, with the pipeline surviving a single
injected agent failure." That gate was P2-6 plus P2-7 together, not either
alone. State it explicitly, show the combined evidence (P2-6's injected
failure test, P2-7's registry proof), and get the user's confirmation before
starting Phase 3. Per `docs/MODEL-EFFORT-GUIDE.md`, Phase 3's first task
(P3-1, evaluation runner) recommends Opus at `max`, since it computes a
headline metric; tell the user the exact `/model` and `/effort` commands and
wait, and remind them after that `max` is session-only.

---

## Definition of done

| Gate clause | Proved by |
|---|---|
| every agent call writes a row with input, output, confidence, model, effort, latency | already true (P2-1/P2-2/P2-3), reconfirmed by `test_a_logged_decision_round_trips_every_field` |
| a query by encounter id returns every decision | Task 1's `SELECT` fix, `test_the_query_returns_every_decision_for_the_encounter_in_order`, and the live `/decisions` call in Task 8 |
