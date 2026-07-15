# P2-1: Prior-Auth Agent — Design

## Context

Phase 2 (`docs/ROADMAP.md`) calls for three independent agent services fronted
by an orchestrator. The initial repo commit already scaffolded stub code for
all of Phase 2, including this agent (`services/agent_prior_auth/agent.py`
and `app.py`), the schema (`shared/schemas.py::PriorAuthOutput`), and registry
logging (`shared/registry.py::log_decision`). None of it has been exercised or
tested.

P2-1's exit criteria from the roadmap:

> Build the agent and its endpoint. Done when a SOAP note yields a valid
> PriorAuthOutput with confidence in [0, 1], and a note with no prior-auth
> items returns an empty list rather than free text.

## Problem with the current scaffold

`agent.py::run` parses the model's response with manual code-fence stripping
(`raw.strip().removeprefix("```json")...`) and a bare `json.loads(raw)` +
`PriorAuthOutput(**...)`, with no error handling. Malformed JSON or an
out-of-range confidence value (Pydantic's `ge=0.0, le=1.0` constraint on
`PriorAuthOutput.confidence`) raises an unhandled exception, which FastAPI
turns into a raw 500 in `app.py`.

This duplicates a problem P1-2 already solved for the intake structuring
step. `services/intake/structure.py` uses `shared.llm.extract_json` and
`shared.llm.MalformedJSONError` (built specifically so this logic lives in
one place, per CLAUDE.md's "one place for data shapes" convention) and wraps
failures in a domain-specific `NoteStructuringError`, which `services/intake/
app.py` catches and turns into an HTTP 502. There are no tests for the
prior-auth agent or its endpoint, so neither exit criterion is currently
verified by anything beyond visual inspection of the code.

## Design

Mirror the intake structuring pattern exactly, rather than inventing a new
error-handling approach for the same class of problem.

### 1. `services/agent_prior_auth/agent.py`

- Replace the manual code-fence stripping and raw `json.loads` with
  `shared.llm.extract_json`.
- Add a `PriorAuthError(ValueError)` class shaped like
  `services/intake/structure.py::NoteStructuringError`: takes `reason` and
  `raw`, stores a truncated (200-char) preview of the raw model output in the
  message, so a failure is diagnosable without dumping full output into logs.
- `run()` catches `MalformedJSONError` from `extract_json` and
  `pydantic.ValidationError` from constructing `PriorAuthOutput`, and
  re-raises both as `PriorAuthError` with an appropriate reason string. This
  covers both malformed JSON and legitimate JSON that fails schema validation
  (e.g. a hallucinated `confidence` of 1.5).
- No retry loop. Unlike P1-2's structuring call, prior-auth is a single
  bounded-reasoning call (S5/high per `shared/llm.py::ROUTING`) with a much
  smaller, more constrained output shape; if this proves to need retries in
  practice, that is a follow-up, not part of this task.
- `log_decision` continues to be called only on success, unchanged from the
  current scaffold.

### 2. `services/agent_prior_auth/app.py`

- Catch `PriorAuthError` in the `/run` endpoint and raise
  `HTTPException(502, str(exc))`, matching `services/intake/app.py`'s
  `NoteStructuringError` → 502 convention. A failure here is the model or the
  pipeline breaking, not a bug in this service, and the orchestrator (P2-6)
  needs a clean signal to isolate a single agent's failure from the other two.

### 3. Tests

- `tests/test_prior_auth_agent.py` (LLM call mocked via `monkeypatch` on
  `shared.llm.call`, mirroring how `tests/test_structure.py` mocks structuring
  calls):
  - Happy path: mocked response with one or more items produces a valid
    `PriorAuthOutput`, and `log_decision` is called with the expected
    `encounter_id`, `note_id`, `agent_name="prior_auth"`, `model`, `effort`,
    `confidence`, and `output`.
  - Empty-items path: mocked response `{"items": [], "confidence": ...}`
    round-trips to `PriorAuthOutput(items=[], ...)`, not free text or a
    parsing error. This is the direct test of the roadmap's second exit
    criterion.
  - Malformed JSON in the mocked response raises `PriorAuthError`.
  - A confidence value outside [0, 1] in the mocked response raises
    `PriorAuthError` (via the wrapped `ValidationError` path).
- `tests/test_agent_prior_auth_app.py` (FastAPI `TestClient`, mirroring
  `tests/test_app.py`):
  - `GET /health` returns `{"status": "ok", "service": "agent_prior_auth"}`.
  - `POST /run` happy path returns 200 with the expected body.
  - `POST /run` where the mocked `run()` raises `PriorAuthError` returns 502
    with the reason in the response detail.

### 4. Live verification

Before calling this task done, run the agent once against the real Anthropic
API with two real SOAP notes: one containing a procedure/medication that
plausibly needs prior auth, and one that plainly does not. Capture both raw
responses as the evidence for the exit criteria, the same way P1-3 first
verified the intake endpoint by hand against a running Postgres before tests
existed to keep it honest. This is a manual verification step, not a new
automated test.

## Out of scope

- `services/agent_coding/agent.py` has the identical manual-stripping
  anti-pattern. Fixing it belongs to P2-3, not this task, but the same
  `PriorAuthError`-style pattern should be reused there (a `CodingError`)
  rather than re-derived.
- No changes to `shared/schemas.py::PriorAuthOutput` or
  `shared/registry.py::log_decision`; both already satisfy this task's needs.
- No retry-on-malformed-JSON loop (see above).
- No changes to k8s manifests or the Dockerfile; P2-5 covers containerization
  and deployment verification.

## Testing

`make test` must stay green with the new test files included, and the new
tests must actually fail against the current scaffold before the fix (i.e.
they are meaningful regression tests, not tautologies against the new code).
