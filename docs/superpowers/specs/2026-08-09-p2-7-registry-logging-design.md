# P2-7: Registry Logging For Every Agent

**Status:** design, approved
**Roadmap task:** P2-7
**Depends on:** P2-1, P2-2, P2-3 (all merged), P2-6 (`docs/superpowers/specs/2026-08-09-p2-6-langgraph-orchestration-design.md`),
the Postgres PVC fix (`k8s/postgres.yaml`)
**Model/effort:** Sonnet 5 at high per `docs/MODEL-EFFORT-GUIDE.md` line 68
("audit correctness matters")

---

## 1. What this task actually changes

The roadmap gate:

> Done when every agent call writes a row to `agent_decisions` with input,
> output, confidence, model, effort, and latency, and a query by encounter id
> returns every decision.

The write half is mostly already done. All three agents call
`shared/registry.py::log_decision` (`services/agent_care_gap/app.py:33`,
`services/agent_coding/agent.py:154`, `services/agent_prior_auth/agent.py:54`),
and it was exercised for real during P2-6's live verification: the registry
already holds rows for `prior_auth`, `care_gap`, and `coding` with real
models, confidences, and latencies.

The read half is not done, and one write-side bug was found while checking
it.

**Bug found: `decisions_for_encounter` drops half the required columns.**

```python
# shared/registry.py, current state
cur = conn.execute(
    "SELECT agent_name, model, output, confidence, created_at "
    "FROM agent_decisions WHERE encounter_id = %s ORDER BY created_at",
    (encounter_id,),
)
```

The gate requires input, output, confidence, model, effort, and latency all
be queryable by encounter id. This `SELECT` returns four of the six:
`model_effort` and `latency_ms` are missing, and `input_ref` is missing too.
The columns exist and are written correctly; only the read query was
incomplete. This is the task's one real defect, not a hypothetical.

**Nothing calls `decisions_for_encounter`.** No endpoint, no test. "A query
by encounter id returns every decision" is currently true only in the sense
that the SQL, if you ran it by hand and widened the SELECT, would work.

This task: widens that query, exposes it as a tested HTTP endpoint, and
documents the one intentional NULL in the schema (`care_gap`'s effort) so it
reads as a decision rather than an oversight.

**What this task does not do.** No changes to `log_decision` or any agent's
write path; they are already correct. No pagination, filtering, or
authentication on the new endpoint; v1 is a single encounter lookup for
audit and debugging. No UI; the dashboard's read path is Phase 4.

---

## 2. The query fix

```python
def decisions_for_encounter(encounter_id: int) -> list[dict]:
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

`note_id` is added beyond the gate's literal six fields because it is already
stored on every row and is the join key back to `notes`; a debugging query
that can name the agent decision but not the note it came from is a
half-finished audit trail.

`input_ref` and `output` are stored as `JSONB` and come back from `psycopg`
as native Python `dict` already, confirmed against the live cluster
(`shared.db.get_conn` round-trip: `input_ref` and `output` both typed `dict`
on read, no manual `json.loads` needed).

---

## 3. The endpoint

`GET /encounters/{encounter_id}/decisions` on the orchestrator, returning
`list[DecisionRecord]`.

**Why the orchestrator and not a new service.** The orchestrator already
knows every encounter it ran (P2-6's `run_agents` is the write path's only
caller besides the agents themselves), needs no new port or Kubernetes
manifest, and keeps the audit read next to the audit write's caller. A
standalone read service for one `GET` route would be disproportionate
machinery; governance has no HTTP surface today, only scripts, and adding
one for this alone is out of scope.

**No existence check against `encounters`.** An encounter that exists but has
not yet been run through `/run` legitimately has zero decisions. An encounter
id that was never created also has zero decisions. Both return `200` with
`[]`. This is a stated simplification, not an oversight: distinguishing "no
decisions yet" from "no such encounter" would need a second query against
`encounters` for a debugging endpoint where that distinction does not change
what the caller does next.

**New schema**, added to `shared/schemas.py` per the one-place-for-shapes
convention (`CLAUDE.md`):

```python
class DecisionRecord(BaseModel):
    """One row from agent_decisions, as returned by a query-by-encounter
    lookup. Not the same shape as PriorAuthOutput etc: this is the audit
    envelope around a decision, not the decision's own artifact schema."""
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

Endpoint:

```python
@app.get("/encounters/{encounter_id}/decisions",
        response_model=list[DecisionRecord])
def get_decisions(encounter_id: int):
    return decisions_for_encounter(encounter_id)
```

Synchronous, not `async def`: this is a single blocking `psycopg` call with
no fan-out, unlike `/run`.

---

## 4. The one intentional NULL

`services/agent_care_gap/app.py` passes `effort=None` because the care-gap
agent is a deterministic rules engine (`model="rules-v1"`), not an LLM call.
No effort level exists to record. `NULL` is the honest value; a sentinel
string like `"n/a"` would be a fabricated data point sitting in a column
otherwise holding real effort levels (`"high"`, `"xhigh"`), and any code that
later validates effort against the five known levels would have to special
case it.

Change: a comment at the call site making the reasoning explicit, so a
future reader sees a decision rather than a suspicious gap.

```python
log_decision(
    encounter_id=inp.encounter_id, note_id=inp.note_id,
    agent_name="care_gap", model="rules-v1",
    effort=None,  # deterministic rules engine, not an LLM call: no
                  # effort level exists to record. NULL is the honest
                  # value here, not a fabricated "n/a" string.
    ...
)
```

No behavior change. `tests/test_registry.py` (section 5) asserts this
specific row reads back with `model_effort is None`, so the documented
decision is also a tested one.

---

## 5. Testing

### `tests/test_registry.py` (new, against a real database)

This module has zero test coverage today. `log_decision` and
`decisions_for_encounter` are exercised against a real Postgres, not mocked,
because the bug this task fixes (a `SELECT` silently dropping columns) is
exactly the class of bug a mocked connection cannot catch.

Guarded by a `needs_db` marker, mirroring the existing `needs_data` pattern
in `tests/test_heldout_guard.py`. Your local dev machine has no standing
`psql` (`docs` env-quirks note); CI's `postgres:16` service and
`db/schema.sql` load make it always available there.

```python
"""P2-7: shared/registry.py against a real database.

Guarded by needs_db, mirroring tests/test_heldout_guard.py's needs_data
pattern: local dev has no standing Postgres, CI's postgres:16 service
always does.
"""
from __future__ import annotations

import psycopg
import pytest

from shared.config import settings
from shared.db import get_conn, insert_encounter, insert_note
from shared.registry import decisions_for_encounter, log_decision


def _db_reachable() -> bool:
    """A bounded timeout matters here specifically: this machine has no
    standing local Postgres (see the env-quirks project note), so an
    unreachable host must fail fast rather than hang test collection on the
    OS's default TCP timeout."""
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
def test_a_rules_engine_decision_reads_back_effort_as_none(encounter_and_note):
    """The one intentional NULL in the schema: care_gap has no effort
    level, and this must stay NULL rather than drift to a sentinel string."""
    encounter_id, note_id = encounter_and_note
    log_decision(
        encounter_id=encounter_id, note_id=note_id, agent_name="care_gap",
        model="rules-v1", effort=None,
        input_ref={}, output={"gaps": []}, confidence=0.9, latency_ms=1,
    )

    row = decisions_for_encounter(encounter_id)[0]
    assert row["model_effort"] is None


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

### Orchestrator endpoint test (`tests/test_orchestrator_integration.py`)

`decisions_for_encounter` is monkeypatched here; the real query is already
proven against a real database above. This test is about the HTTP surface:
status code, response shape, and the empty-list case.

```python
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
    monkeypatch.setattr("services.orchestrator.app.decisions_for_encounter",
                        lambda encounter_id: [])
    resp = client.get("/encounters/999999/decisions")
    assert resp.status_code == 200
    assert resp.json() == []
```

### Live, in-cluster (recorded as roadmap evidence)

Reusing the seeded encounter from P2-6's live run, now on the PVC-backed
database so it persists across this and future verification:

1. `POST /run` for a real note, confirm three artifacts as in P2-6.
2. `GET /encounters/{id}/decisions`, confirm exactly three rows, one per
   agent, each carrying a real `model`, and `care_gap`'s `model_effort` is
   `null`.
3. This is the gate's second clause proved end to end, not only in pytest.

---

## 6. Files

| File | Change |
|---|---|
| `shared/registry.py` | widen `decisions_for_encounter`'s `SELECT` |
| `shared/schemas.py` | add `DecisionRecord` |
| `services/orchestrator/app.py` | add `GET /encounters/{id}/decisions` |
| `services/agent_care_gap/app.py` | comment only, on the existing `effort=None` |
| `tests/test_registry.py` | new |
| `tests/test_orchestrator_integration.py` | two new tests |
| `docs/ROADMAP.md` | P2-7 evidence entry |

---

## 7. Known gaps, stated rather than discovered later

1. **No pagination.** An encounter with an unusually large number of agent
   runs (retries, replays) returns all of them in one response. Not a
   concern at current scale; worth revisiting if replay logging is added.
2. **No auth on the new endpoint.** Consistent with every other endpoint in
   the system today; the whole API is unauthenticated. Out of scope here.
3. **The empty-list-for-unknown-encounter behavior is a debugging-endpoint
   simplification**, not a REST purity argument. If this endpoint ever
   serves a client that needs to distinguish "not created" from "no
   decisions yet," it will need the `encounters` existence check this spec
   deliberately omits.
