# P2-1: Prior-Auth Agent Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the scaffolded Prior-Auth Agent (`services/agent_prior_auth/`) satisfy P2-1's exit criteria with real error handling and test coverage, by mirroring the P1-2 intake-structuring pattern instead of the scaffold's unhandled manual JSON parsing.

**Architecture:** Add a `PriorAuthError` domain exception to `services/agent_prior_auth/agent.py`, modeled directly on `services/intake/structure.py::NoteStructuringError`, using the shared `extract_json`/`MalformedJSONError` helpers instead of hand-rolled code-fence stripping. The FastAPI endpoint in `services/agent_prior_auth/app.py` catches that exception and returns HTTP 502, matching `services/intake/app.py`'s convention. Two new test files cover the agent function (LLM mocked) and the endpoint (agent function mocked), and a final manual step exercises the real Anthropic API once.

**Tech Stack:** Python, pydantic, FastAPI, pytest, `TestClient`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-14-p2-1-prior-auth-agent-design.md`

**Model/effort:** per `docs/MODEL-EFFORT-GUIDE.md`, P2-1 recommends `/model sonnet` and `/effort high`. This matches Claude Code's default, so no session change is needed, but CLAUDE.md's notify convention requires stating it regardless.

---

## Chunk 1: Prior-Auth Agent error handling and tests

### Task 1: Agent-level error handling, TDD

**Files:**
- Modify: `services/agent_prior_auth/agent.py` (full rewrite of its body, same file)
- Test: `tests/test_prior_auth_agent.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prior_auth_agent.py`:

```python
"""P2-1: prior-auth agent produces a valid PriorAuthOutput, and malformed
model output raises a clear parse error. The LLM call and registry logging
are faked here; a real call is exercised separately by hand (see the spec's
live-verification step).
"""
from __future__ import annotations

import pytest

from services.agent_prior_auth.agent import PriorAuthError, run
from shared.schemas import AgentInput, SoapNote

SOAP = SoapNote(subjective="Knee pain after a fall.",
                objective="Swelling and tenderness noted.",
                assessment="Suspected meniscus tear.",
                plan="Order MRI, refer to orthopedics.")

INPUT = AgentInput(encounter_id=1, note_id=1, soap=SOAP)


def _fake_call(monkeypatch, response: str):
    monkeypatch.setattr(
        "services.agent_prior_auth.agent.call",
        lambda component, system, user, max_tokens=1500,
               temperature=None: response,
    )


def _fake_log_decision(monkeypatch):
    """Returns the list of kwargs each log_decision call received."""
    calls: list[dict] = []

    def fake(**kwargs):
        calls.append(kwargs)
        return 1

    monkeypatch.setattr("services.agent_prior_auth.agent.log_decision", fake)
    return calls


def test_valid_json_produces_prior_auth_output(monkeypatch):
    _fake_call(monkeypatch, '{"items": [{"item": "MRI knee", '
                            '"reason": "advanced imaging", '
                            '"justification": "suspected meniscus tear"}], '
                            '"confidence": 0.8}')
    log_calls = _fake_log_decision(monkeypatch)

    out = run(INPUT)

    assert len(out.items) == 1
    assert out.items[0].item == "MRI knee"
    assert out.confidence == 0.8
    assert len(log_calls) == 1
    assert log_calls[0]["agent_name"] == "prior_auth"
    assert log_calls[0]["encounter_id"] == 1
    assert log_calls[0]["note_id"] == 1
    assert log_calls[0]["confidence"] == 0.8


def test_no_prior_auth_items_returns_empty_list_not_free_text(monkeypatch):
    _fake_call(monkeypatch, '{"items": [], "confidence": 0.95}')
    _fake_log_decision(monkeypatch)

    out = run(INPUT)

    assert out.items == []
    assert out.confidence == 0.95


def test_invalid_json_raises_clear_parse_error(monkeypatch):
    _fake_call(monkeypatch, "this is not json at all")
    _fake_log_decision(monkeypatch)

    with pytest.raises(PriorAuthError, match="not valid JSON"):
        run(INPUT)


def test_json_array_instead_of_object_raises_clear_parse_error(monkeypatch):
    _fake_call(monkeypatch, '[{"item": "x"}]')
    _fake_log_decision(monkeypatch)

    with pytest.raises(PriorAuthError, match="not an object"):
        run(INPUT)


def test_confidence_out_of_range_raises_clear_parse_error(monkeypatch):
    _fake_call(monkeypatch, '{"items": [], "confidence": 1.5}')
    _fake_log_decision(monkeypatch)

    with pytest.raises(PriorAuthError, match="PriorAuthOutput schema"):
        run(INPUT)


def test_parse_error_preview_is_truncated(monkeypatch):
    long_garbage = "x" * 500
    _fake_call(monkeypatch, long_garbage)
    _fake_log_decision(monkeypatch)

    with pytest.raises(PriorAuthError) as exc_info:
        run(INPUT)
    assert "..." in str(exc_info.value)
    assert len(str(exc_info.value)) < 500
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_prior_auth_agent.py -v`

Expected: collection error, `ImportError: cannot import name 'PriorAuthError' from 'services.agent_prior_auth.agent'`. `PriorAuthError` does not exist yet, so every test in the file fails at collection. This is the correct starting failure.

- [ ] **Step 3: Rewrite `services/agent_prior_auth/agent.py`**

Replace the full file contents with:

```python
"""Prior-Auth Agent: flag items needing prior authorization, draft a snippet."""
from __future__ import annotations

import time

from pydantic import ValidationError

from shared.llm import MalformedJSONError, call, extract_json, ROUTING
from shared.registry import log_decision
from shared.schemas import AgentInput, PriorAuthOutput

_SYSTEM = (
    "You review a SOAP note and identify procedures or medications that "
    "commonly require prior authorization. Return only JSON: "
    '{"items": [{"item": "", "reason": "", "justification": ""}], '
    '"confidence": 0.0}. Confidence is your calibrated certainty in [0, 1]. '
    "If nothing requires prior authorization, return an empty items list."
)


class PriorAuthError(ValueError):
    """Raised when the model's response cannot be parsed into a PriorAuthOutput.

    Carries a truncated preview of the raw output so a failure is
    diagnosable without dumping full model output into logs.
    """

    def __init__(self, reason: str, raw: str):
        preview = raw[:200] + ("..." if len(raw) > 200 else "")
        super().__init__(
            f"Prior-auth parsing failed: {reason}. Raw output: {preview!r}")


def run(inp: AgentInput) -> PriorAuthOutput:
    model, effort = ROUTING["prior_auth"]
    started = time.perf_counter()
    raw = call("prior_auth", system=_SYSTEM, user=inp.soap.model_dump_json())

    try:
        data = extract_json(raw)
    except MalformedJSONError as exc:
        raise PriorAuthError(exc.reason, raw) from exc

    if not isinstance(data, dict):
        raise PriorAuthError("JSON was not an object", raw)

    try:
        out = PriorAuthOutput(**data)
    except ValidationError as exc:
        raise PriorAuthError(
            f"did not match the PriorAuthOutput schema ({exc})", raw) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    log_decision(
        encounter_id=inp.encounter_id, note_id=inp.note_id,
        agent_name="prior_auth", model=model, effort=effort,
        input_ref=inp.soap.model_dump(), output=out.model_dump(),
        confidence=out.confidence, latency_ms=latency_ms,
    )
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_prior_auth_agent.py -v`

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/agent_prior_auth/agent.py tests/test_prior_auth_agent.py
git commit -m "$(cat <<'EOF'
feat(P2-1): prior-auth agent uses shared JSON parsing, raises PriorAuthError

Replaces the scaffold's manual code-fence stripping and unhandled
json.loads/Pydantic construction with the same extract_json /
MalformedJSONError / isinstance-guard pattern P1-2 already uses in
services/intake/structure.py, so malformed or schema-invalid model
output fails predictably instead of raising an uncaught exception.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Endpoint 502 handling, TDD

**Files:**
- Modify: `services/agent_prior_auth/app.py`
- Test: `tests/test_prior_auth_app.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prior_auth_app.py`:

```python
"""P2-1: the prior-auth agent's HTTP endpoint. run() is mocked here; its
own correctness is covered by tests/test_prior_auth_agent.py. This file's
job is the wiring: does a request reach run() with the right argument, and
does a PriorAuthError become a 502, not a 500.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.agent_prior_auth.agent import PriorAuthError
from services.agent_prior_auth.app import app
from shared.schemas import PriorAuthOutput, SoapNote

SOAP = SoapNote(subjective="s", objective="o", assessment="a", plan="p")
OUTPUT = PriorAuthOutput(items=[], confidence=0.9)


@pytest.fixture
def client():
    return TestClient(app)


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "agent_prior_auth"}


def test_run_happy_path_returns_200(client, monkeypatch):
    monkeypatch.setattr("services.agent_prior_auth.app.run", lambda inp: OUTPUT)

    resp = client.post("/run", json={
        "encounter_id": 1, "note_id": 1, "soap": SOAP.model_dump(),
    })

    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["confidence"] == 0.9


def test_run_failure_is_a_502_not_a_500(client, monkeypatch):
    def raise_error(inp):
        raise PriorAuthError("not valid JSON (test)", "garbage")

    monkeypatch.setattr("services.agent_prior_auth.app.run", raise_error)

    resp = client.post("/run", json={
        "encounter_id": 1, "note_id": 1, "soap": SOAP.model_dump(),
    })

    assert resp.status_code == 502
    assert "not valid JSON" in resp.json()["detail"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_prior_auth_app.py -v`

Expected: `test_health_returns_ok` and `test_run_happy_path_returns_200` pass already (the current scaffold happens to satisfy them), but `test_run_failure_is_a_502_not_a_500` fails: `TestClient` re-raises the unhandled `PriorAuthError` instead of returning a response, so the test errors with `PriorAuthError` instead of asserting on a 502.

- [ ] **Step 3: Update `services/agent_prior_auth/app.py`**

Replace the full file contents with:

```python
from fastapi import FastAPI, HTTPException

from services.agent_prior_auth.agent import PriorAuthError, run
from shared.schemas import AgentInput, PriorAuthOutput

app = FastAPI(title="Care Ops Copilot - Prior Auth Agent")


@app.get("/health")
def health():
    return {"status": "ok", "service": "agent_prior_auth"}


@app.post("/run", response_model=PriorAuthOutput)
def run_endpoint(inp: AgentInput):
    try:
        return run(inp)
    except PriorAuthError as exc:
        raise HTTPException(502, str(exc)) from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_prior_auth_app.py -v`

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/agent_prior_auth/app.py tests/test_prior_auth_app.py
git commit -m "$(cat <<'EOF'
feat(P2-1): prior-auth endpoint returns 502 on PriorAuthError

Matches services/intake/app.py's NoteStructuringError -> 502
convention: a parsing failure is the model or pipeline breaking, not
a bug in this service, and the orchestrator (P2-6) needs a clean
signal to isolate a single agent's failure from the other two.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Full regression run

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `make test`

Expected: all tests pass, including the two new files, with no change in outcome for any other test file.

- [ ] **Step 2: Run lint**

Run: `make lint`

Expected: clean.

No commit for this task; it is a checkpoint. If either command fails, fix the regression before moving on.

---

### Task 4: Live verification against the real API

**Files:** none (manual verification, not committed)

This step produces the actual evidence for the roadmap's exit criteria: "a SOAP note yields a valid PriorAuthOutput with confidence in [0, 1], and a note with no prior-auth items returns an empty list rather than free text." Everything above is a mocked test; this is the one live check.

- [ ] **Step 1: Ensure Postgres is running**

`run()` calls `log_decision`, which writes to the `agent_decisions` table. Start the database first:

```bash
docker compose up -d db
make db-init
```

- [ ] **Step 2: Ensure `.env` has a real `ANTHROPIC_API_KEY`**

Confirm `.env` (not `.env.example`) has a working key, per the Setup section of `CLAUDE.md`.

- [ ] **Step 3: Run one note that plausibly needs prior auth, and one that plainly does not**

`agent_decisions` has `NOT NULL REFERENCES` to both `encounters(id)` and `notes(id)` (see `db/schema.sql`), and `make db-init` does not seed any rows into either table. A hardcoded `encounter_id`/`note_id` would hit that foreign-key constraint the moment `log_decision` runs inside `run()`. Use the existing `insert_encounter`/`insert_note` helpers from `shared/db.py` (the same ones `services/intake/app.py` uses) to create real rows first, instead of hand-picking an ID:

```bash
python - <<'EOF'
from shared.db import insert_encounter, insert_note
from services.agent_prior_auth.agent import run
from shared.schemas import AgentInput, SoapNote

needs_auth_soap = SoapNote(
    subjective="Follow-up for chronic lower back pain, conservative "
               "care has failed over 6 weeks.",
    objective="Reduced lumbar flexion, positive straight leg raise.",
    assessment="Suspected lumbar disc herniation.",
    plan="Order lumbar MRI; refer to physical therapy in the interim.",
)
no_auth_soap = SoapNote(
    subjective="Here for a routine wellness check, feeling well.",
    objective="Vitals normal, exam unremarkable.",
    assessment="Healthy adult, no acute issues.",
    plan="Routine follow-up in 12 months.",
)

for label, soap in [("NEEDS PRIOR AUTH", needs_auth_soap),
                    ("NO PRIOR AUTH", no_auth_soap)]:
    encounter_id = insert_encounter(None, "transcript")
    note_id = insert_note(encounter_id, soap.model_dump(),
                          "manual-verification", "high")
    inp = AgentInput(encounter_id=encounter_id, note_id=note_id, soap=soap)
    print(f"=== {label} ===")
    print(run(inp).model_dump_json(indent=2))
EOF
```

- [ ] **Step 4 (evidence): Paste both outputs into the PR description or hand off directly in the chat where this task is reviewed**

Confirm by inspection:
- The "needs auth" case produced a non-empty `items` list with a `confidence` in `[0, 1]`.
- The "no auth" case produced `"items": []`, not free text, with a `confidence` in `[0, 1]`.

This step is not committed as a fixture; it is the manual sign-off that closes out P2-1's exit criteria, the same way P1-3 was first verified by hand before automated tests existed.

---

## Definition of done

- `tests/test_prior_auth_agent.py` and `tests/test_prior_auth_app.py` exist and pass.
- `make test` and `make lint` are clean.
- Both live-verification outputs have been captured and confirmed to satisfy the roadmap's P2-1 exit criteria.
- State the P2-1 exit criteria, show the test output and the live-verification evidence, and get explicit user confirmation before starting P2-2, per CLAUDE.md's phase-gate rule (P2-1 is a task within Phase 2, not the phase's own exit gate, but the same evidence-before-advancing discipline applies task to task).
