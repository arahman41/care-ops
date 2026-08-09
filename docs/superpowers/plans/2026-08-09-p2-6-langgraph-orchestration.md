# P2-6: LangGraph Orchestration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `services/orchestrator/` from a sequential `httpx` loop that
falsely claims to be parallel into a real compiled LangGraph `StateGraph` whose
three agent calls run concurrently and whose failures are isolated per agent,
proved by a socket-level integration test and one live in-cluster run.

**Architecture:** One `StateGraph` fans out from `START` to three
`call_<agent>` nodes in a single superstep and joins at `END`. Each node owns
one agent: it resolves that agent's URL from `settings` at call time, POSTs the
payload over HTTP, validates the response against that agent's own pydantic
contract, and converts every failure class into an entry in a reducer-merged
`errors` channel rather than raising. The reducer is what allows more than one
agent to fail in the same superstep without aborting the graph.

**Tech Stack:** Python 3.12 (CI and image) / 3.10.11 (local venv), langgraph
1.2.10, FastAPI, httpx 0.28.1, pydantic 2.10.4, uvicorn 0.34.0, pytest.

**Spec:** `docs/superpowers/specs/2026-08-09-p2-6-langgraph-orchestration-design.md`

**Model/effort:** per `docs/MODEL-EFFORT-GUIDE.md` line 67, P2-6 recommends
Opus at `xhigh` ("multi-service control and failure isolation"). Confirm the
session matches before starting.

**Branch:** `p2-6-langgraph-orchestration`, already created, already carries
the spec commit `7369ec2`.

---

## Read this before Task 1

Five things in this plan are easy to "clean up" into a bug that no test
catches unless the test is written exactly as specified.

**1. The `errors` reducer is load-bearing.** `errors` is
`Annotated[dict[str, str], _merge_errors]` because three nodes can write it in
the same superstep. Removing the annotation does not cause a merge conflict at
worst, it raises `InvalidUpdateError` and aborts **the whole graph**, which is
the exact failure the roadmap gate forbids. This was verified by probe on both
candidate langgraph versions. Task 11 is the test that locks it in.

**2. Each node validates its own response. Do not move validation up to
`PipelineResult`.** The old code passed raw agent JSON into `PipelineResult`,
so pydantic validated at response-construction time and one agent's malformed
`200` destroyed all three artifacts. Validating inside the node is what keeps
that failure local. Same trust-boundary reasoning as the P2-3
`ModelCodingPayload` / `CodingOutput` split.

**3. Agent URLs resolve per call, never at import.** A module-level
`_AGENTS = {...f"{settings.prior_auth_url}/run"...}` freezes the URL at import
time, which silently breaks every test that retargets a stub server and makes
the setting a lie. Keep `_agent_url()` a function.

**4. The integration test uses real sockets on purpose.** `httpx.ASGITransport`
would be faster and would not catch connection-refused, read-timeout, or
serialization failures. If it looks like wasted ceremony, re-read spec
section 7.

**5. No em dashes in any generated text**, per `CLAUDE.md`. Every existing spec
and plan in this repo has zero. Use a comma, a colon, or a full stop.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `services/orchestrator/graph.py` | the graph, its nodes, and their failure handling | rewritten |
| `services/orchestrator/app.py` | HTTP surface only, no orchestration logic | `/run` becomes async |
| `shared/config.py` | one place for settings | add `agent_timeout_seconds` |
| `services/orchestrator/requirements.txt` | image deps | pin the langgraph family |
| `requirements-dev.txt` | CI deps, so CI runs the real graph | pin the langgraph family |
| `tests/test_orchestrator_graph.py` | pure units: reducer, error formatter, URL resolution, graph shape | new |
| `tests/test_orchestrator_integration.py` | real sockets: fan-out, concurrency, isolation | new |
| `docs/ROADMAP.md` | P2-6 evidence entry | append |

`graph.py` stays one file. It is about 120 lines and everything in it is one
responsibility (turn a payload into three artifacts plus errors). Splitting the
node factory out would separate things that change together.

---

## Chunk 1: Dependencies and configuration

### Task 1: Pin the langgraph family and install it

**Files:**
- Modify: `services/orchestrator/requirements.txt`
- Modify: `requirements-dev.txt`

- [ ] **Step 1: Replace the two stale lines in the orchestrator requirements**

`services/orchestrator/requirements.txt` becomes:

```
-r ../../requirements.txt
# P2-6: the whole langgraph family is pinned, not just langgraph. The previous
# `langgraph==0.2.60` left its transitives floating, so a rebuild resolved
# langgraph-checkpoint 2.1.2, a 2025 release against a Dec-2024 langgraph and a
# combination nobody upstream tests. These six versions were verified together
# against this repo's pydantic/fastapi/httpx pins with `pip check` clean.
langgraph==1.2.10
langgraph-checkpoint==4.2.0
langgraph-prebuilt==1.1.0
langgraph-sdk==0.4.2
langchain-core==1.5.3
langchain-protocol==0.0.18
```

- [ ] **Step 2: Add the same block to `requirements-dev.txt`**

Append after the existing `-r governance/requirements.txt` line, with this
comment, because the reason it is duplicated is not obvious:

```
# P2-6: mirrored from services/orchestrator/requirements.txt so CI exercises
# the real compiled graph. Without this, tests import langgraph and fail at
# collection while the image builds fine, or worse, the graph is never tested.
langgraph==1.2.10
langgraph-checkpoint==4.2.0
langgraph-prebuilt==1.1.0
langgraph-sdk==0.4.2
langchain-core==1.5.3
langchain-protocol==0.0.18
```

- [ ] **Step 3: Install and verify no conflicts**

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pip check
```

Expected: `No broken requirements found.`

- [ ] **Step 4: Verify the existing suite still passes after the upgrade**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Expected: same pass/fail counts as before the install. If anything newly
fails, stop: the dependency upgrade is the cause, and that is a finding worth
reporting, not working around.

- [ ] **Step 5: Commit**

```bash
git add requirements-dev.txt services/orchestrator/requirements.txt
git commit -m "build(P2-6): pin the langgraph family, upgrade 0.2.60 to 1.2.10"
```

---

### Task 2: Add the per-agent timeout setting

**Files:**
- Modify: `shared/config.py`
- Test: `tests/test_orchestrator_graph.py` (created here)

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_graph.py`:

```python
"""P2-6: the orchestrator graph's pure units.

Sockets, real HTTP, and failure isolation live in
tests/test_orchestrator_integration.py. This file covers the pieces that can
be tested without a server: the errors reducer, the error formatter, URL
resolution, and the compiled graph's shape.
"""
from __future__ import annotations

from shared.config import settings


def test_agent_timeout_has_a_default_justified_by_measurement():
    """60s is 3.9x the routed coding config's measured p95 of 15,517ms
    (P2-4 artifact coding_20260807T214249Z.json, 113 held-out notes)."""
    assert settings.agent_timeout_seconds == 60.0
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_orchestrator_graph.py -q
```

Expected: `AttributeError: 'Settings' object has no attribute 'agent_timeout_seconds'`

- [ ] **Step 3: Add the setting**

In `shared/config.py`, after the four service URL lines:

```python
    # P2-6: how long the orchestrator waits on one agent before recording a
    # timeout and moving on. 60s is 3.9x the p95 (15,517ms) and 3.3x the max
    # (18,102ms) measured for the routed coding configuration over 113
    # held-out notes in P2-4. Prior-auth and care-gap latency are unmeasured.
    # Configurable so the timeout test runs in a second, not a minute.
    agent_timeout_seconds: float = 60.0
```

- [ ] **Step 4: Run it and watch it pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_orchestrator_graph.py -q
```

Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add shared/config.py tests/test_orchestrator_graph.py
git commit -m "feat(P2-6): add agent_timeout_seconds, justified against P2-4 latency"
```

---

## Chunk 2: The graph

### Task 3: The errors reducer

**Files:**
- Create: `services/orchestrator/graph.py` (replacing the current contents)
- Test: `tests/test_orchestrator_graph.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_graph.py`:

```python
from services.orchestrator.graph import _merge_errors


def test_merge_errors_combines_two_failures():
    assert _merge_errors({"coding": "a"}, {"prior_auth": "b"}) == {
        "coding": "a", "prior_auth": "b"}


def test_merge_errors_does_not_mutate_either_input():
    left, right = {"coding": "a"}, {"prior_auth": "b"}
    _merge_errors(left, right)
    assert left == {"coding": "a"}
    assert right == {"prior_auth": "b"}


def test_merge_errors_starts_from_empty():
    assert _merge_errors({}, {"coding": "a"}) == {"coding": "a"}
```

Mutation matters: LangGraph holds the channel value across the superstep, and
a reducer that mutates its left argument corrupts state in a way that only
shows up under concurrency.

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_orchestrator_graph.py -q`
Expected: `ImportError: cannot import name '_merge_errors'`

- [ ] **Step 3: Start the new `graph.py` with its docstring and the reducer**

Replace the whole file:

```python
"""LangGraph fan-out to the three agents, then collect their artifacts.

v1 routing is deterministic: every note visits all three agents, in one
superstep, so the pipeline's wall clock is the slowest agent rather than the
sum of all three. The edges here are the seam where richer routing lands in
v2.

Two properties are load-bearing and are easy to break by tidying:

1. `errors` carries a reducer. Three nodes can write it in the same superstep
   and LangGraph raises InvalidUpdateError on an unreduced concurrent write,
   which would abort the whole graph. Isolation depends on this.
2. Each node validates its own agent's response against that agent's own
   contract, inside the node. Validating later, at PipelineResult, lets one
   agent's malformed 200 destroy all three artifacts.
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from shared.config import settings
from shared.schemas import CareGapOutput, CodingOutput, PriorAuthOutput


def _merge_errors(left: dict[str, str],
                  right: dict[str, str]) -> dict[str, str]:
    """Reducer for the `errors` channel. Returns a new dict: mutating `left`
    would corrupt the channel value mid-superstep."""
    return {**left, **right}
```

- [ ] **Step 4: Run and watch it pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_orchestrator_graph.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add services/orchestrator/graph.py tests/test_orchestrator_graph.py
git commit -m "feat(P2-6): errors reducer for concurrent per-agent failures"
```

---

### Task 4: The failure formatter

**Files:**
- Modify: `services/orchestrator/graph.py`
- Test: `tests/test_orchestrator_graph.py`

- [ ] **Step 1: Write the failing tests**

```python
import httpx
import pytest

from services.orchestrator.graph import _describe

URL = "http://agent-coding:8000/run"


def test_a_read_timeout_still_says_something():
    """str(httpx.ReadTimeout("")) is the empty string, so the naive
    f"{type(exc).__name__}: {exc}" logs "ReadTimeout: " and the audit trail
    records nothing for the most likely cluster failure."""
    msg = _describe(httpx.ReadTimeout(""), URL, 60.0)
    assert "ReadTimeout" in msg
    assert URL in msg
    assert "60.0s" in msg
    assert not msg.endswith(": ")


def test_a_status_error_carries_the_status_code():
    request = httpx.Request("POST", URL)
    response = httpx.Response(502, text="upstream model error", request=request)
    msg = _describe(
        httpx.HTTPStatusError("", request=request, response=response),
        URL, 60.0)
    assert "502" in msg
    assert "upstream model error" in msg


def test_a_long_agent_body_is_truncated():
    request = httpx.Request("POST", URL)
    response = httpx.Response(500, text="x" * 5000, request=request)
    msg = _describe(
        httpx.HTTPStatusError("", request=request, response=response),
        URL, 60.0)
    assert len(msg) < 400


def test_a_connect_error_keeps_its_own_detail():
    msg = _describe(httpx.ConnectError("connection refused"), URL, 60.0)
    assert "ConnectError" in msg
    assert "connection refused" in msg
```

- [ ] **Step 2: Run and watch it fail**

Expected: `ImportError: cannot import name '_describe'`

- [ ] **Step 3: Implement**

Add to `graph.py` after `_merge_errors`:

```python
_BODY_CHARS = 200


def _describe(exc: Exception, url: str, timeout: float) -> str:
    """One format for every failure entry, so no call site invents its own.

    Always names the exception class and the URL that failed, which is what
    makes a cluster DNS problem distinguishable from an agent bug. str() alone
    is not enough: str(httpx.ReadTimeout("")) is the empty string.
    """
    name = type(exc).__name__
    detail = str(exc).strip()
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:_BODY_CHARS].strip()
        detail = f"HTTP {exc.response.status_code} {body}".strip()
    elif isinstance(exc, httpx.TimeoutException) and not detail:
        detail = f"no response within {timeout}s"
    return f"{name} calling {url}: {detail}" if detail else f"{name} calling {url}"
```

- [ ] **Step 4: Run and watch it pass**

Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(P2-6): failure formatter that survives an empty str(exc)"
```

---

### Task 5: Per-call URL resolution

**Files:**
- Modify: `services/orchestrator/graph.py`
- Test: `tests/test_orchestrator_graph.py`

- [ ] **Step 1: Write the failing tests**

```python
from services.orchestrator.graph import _agent_url


def test_agent_url_defaults_to_the_kubernetes_service_dns_name():
    assert _agent_url("coding_url") == "http://agent-coding:8000/run"


def test_agent_url_is_read_per_call_not_frozen_at_import(monkeypatch):
    """The integration test retargets these at localhost stubs. If the URL is
    computed at import time, that silently keeps hitting the cluster names."""
    monkeypatch.setattr(settings, "coding_url", "http://127.0.0.1:9999")
    assert _agent_url("coding_url") == "http://127.0.0.1:9999/run"


def test_agent_url_tolerates_a_trailing_slash(monkeypatch):
    monkeypatch.setattr(settings, "coding_url", "http://127.0.0.1:9999/")
    assert _agent_url("coding_url") == "http://127.0.0.1:9999/run"
```

- [ ] **Step 2: Run and watch it fail**

Expected: `ImportError: cannot import name '_agent_url'`

- [ ] **Step 3: Implement**

```python
def _agent_url(setting_name: str) -> str:
    """Resolved per call, never at import: a module-level constant freezes the
    cluster DNS name and makes the setting unoverridable."""
    return f"{getattr(settings, setting_name).rstrip('/')}/run"
```

- [ ] **Step 4: Run and watch it pass**

Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(P2-6): resolve agent URLs per call so settings stay live"
```

---

### Task 6: State, nodes, and the compiled graph

**Files:**
- Modify: `services/orchestrator/graph.py`
- Test: `tests/test_orchestrator_graph.py`

- [ ] **Step 1: Write the failing tests**

```python
from services.orchestrator.graph import AGENTS, PipelineState, _GRAPH


def test_the_graph_has_exactly_one_node_per_agent():
    real = {n for n in _GRAPH.nodes if not n.startswith("__")}
    assert real == {"call_prior_auth", "call_care_gap", "call_coding"}


def test_every_node_name_differs_from_the_state_key_it_writes():
    """langgraph 0.2.60 raised ValueError on a collision. 1.2.10 permits it.
    The prefix is kept as a convention: a node is an action, a state key is an
    artifact."""
    keys = set(PipelineState.__annotations__)
    assert {n for n in _GRAPH.nodes if not n.startswith("__")}.isdisjoint(keys)


def test_state_carries_a_key_for_every_agent_plus_errors():
    assert set(PipelineState.__annotations__) == {
        "payload", "prior_auth", "care_gap", "coding", "errors"}


def test_every_agent_is_wired_to_its_own_schema():
    assert {a[0] for a in AGENTS} == {"prior_auth", "care_gap", "coding"}
    assert [a[2].__name__ for a in AGENTS] == [
        "PriorAuthOutput", "CareGapOutput", "CodingOutput"]
```

- [ ] **Step 2: Run and watch it fail**

Expected: `ImportError: cannot import name 'AGENTS'`

- [ ] **Step 3: Implement the rest of `graph.py`**

```python
class PipelineState(TypedDict):
    """`errors` is the only key with more than one possible writer, which is
    why it is the only one carrying a reducer. The three artifact keys have
    exactly one writer each, so LastValue is correct for them."""
    payload: dict
    prior_auth: dict | None
    care_gap: dict | None
    coding: dict | None
    errors: Annotated[dict[str, str], _merge_errors]


# (state key, settings attribute, the contract that agent must satisfy)
AGENTS: tuple[tuple[str, str, type[BaseModel]], ...] = (
    ("prior_auth", "prior_auth_url", PriorAuthOutput),
    ("care_gap", "care_gap_url", CareGapOutput),
    ("coding", "coding_url", CodingOutput),
)


def _make_node(agent: str, setting_name: str, schema: type[BaseModel]):
    """One code path for all three agents.

    Every failure class becomes an `errors` entry rather than an exception: a
    raise here aborts the graph and takes the other two agents with it. That
    covers connection refused, DNS failure, read timeout, non-2xx status, a
    body that is not JSON, and a 200 whose shape violates the contract.
    """
    async def node(state: PipelineState) -> dict[str, Any]:
        url = _agent_url(setting_name)
        timeout = settings.agent_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=state["payload"])
                response.raise_for_status()
                artifact = schema.model_validate(response.json())
        except Exception as exc:
            return {"errors": {agent: _describe(exc, url, timeout)}}
        return {agent: artifact.model_dump()}

    node.__name__ = f"call_{agent}"
    return node


def _build_graph():
    builder = StateGraph(PipelineState)
    for agent, setting_name, schema in AGENTS:
        node_name = f"call_{agent}"
        builder.add_node(node_name, _make_node(agent, setting_name, schema))
        builder.add_edge(START, node_name)      # fan out
        builder.add_edge(node_name, END)        # join
    return builder.compile()


# Compiled once at import. Safe because nodes resolve their URL and timeout
# per call, so nothing about the environment is baked in here.
_GRAPH = _build_graph()


async def run_agents(payload: dict) -> dict:
    """Run all three agents concurrently. Never raises for an agent failure:
    the caller reads `errors` to find out what broke."""
    initial: PipelineState = {
        "payload": payload,
        "prior_auth": None,
        "care_gap": None,
        "coding": None,
        "errors": {},
    }
    return await _GRAPH.ainvoke(initial)
```

Note the broad `except Exception`. It is deliberate and is the isolation
mechanism. `asyncio.CancelledError` derives from `BaseException` in Python
3.8+, so cancellation still propagates.

- [ ] **Step 4: Run and watch it pass**

Expected: `15 passed`

- [ ] **Step 5: Lint**

Run: `.venv/Scripts/python.exe -m ruff check services/orchestrator/`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(P2-6): compile the fan-out StateGraph with per-node isolation"
```

---

### Task 7: Make the endpoint async

**Files:**
- Modify: `services/orchestrator/app.py`

- [ ] **Step 1: Change the endpoint**

`run_agents` is now a coroutine, so the endpoint must await it. Calling it
without `await` returns a coroutine object and every artifact silently becomes
a validation error.

```python
@app.post("/run", response_model=PipelineResult)
async def run(inp: AgentInput):
    out = await run_agents(inp.model_dump())
    return PipelineResult(
        encounter_id=inp.encounter_id,
        note_id=inp.note_id,
        prior_auth=out["prior_auth"],
        care_gap=out["care_gap"],
        coding=out["coding"],
        errors=out["errors"],
    )
```

- [ ] **Step 2: Verify the health endpoint still answers**

```bash
.venv/Scripts/python.exe -c "from fastapi.testclient import TestClient; from services.orchestrator.app import app; r=TestClient(app).get('/health'); print(r.status_code, r.json())"
```

Expected: `200 {'status': 'ok', 'service': 'orchestrator'}`

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(P2-6): await the graph from POST /run"
```

---

## Chunk 3: Integration over real sockets

### Task 8: Stub server harness and the happy path

**Files:**
- Create: `tests/test_orchestrator_integration.py`

- [ ] **Step 1: Write the harness and the happy-path test**

```python
"""P2-6: the orchestrator talking to three agents over real HTTP.

What is real here: the socket, the HTTP round trip, JSON serialization, the
pydantic contracts on both ends, and every failure path. What is not real:
the agents themselves. Each stub returns a genuine PriorAuthOutput /
CareGapOutput / CodingOutput built from shared.schemas, so a contract change
breaks these tests, but no agent's own logic runs here. That logic is covered
by tests/test_prior_auth_agent.py, test_care_gap_rules.py, and
test_coding_agent.py.

The servers are real uvicorn servers on ephemeral ports rather than
httpx.ASGITransport, because ASGITransport cannot produce a connection
refused, a read timeout, or a serialization failure, and those are three of
the five isolation cases the roadmap gate asks about.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from shared.config import settings
from shared.schemas import (CareGapItem, CareGapOutput, CareGapSource,
                            CodeSuggestion, CodingOutput, PriorAuthItem,
                            PriorAuthOutput)

SOAP = {"subjective": "s", "objective": "o", "assessment": "a", "plan": "p"}
PAYLOAD = {"encounter_id": 1, "note_id": 1, "soap": SOAP}

PRIOR_AUTH = PriorAuthOutput(
    items=[PriorAuthItem(item="MRI lumbar spine", reason="commonly reviewed",
                         justification="six weeks of conservative therapy")],
    confidence=0.82)

CARE_GAP = CareGapOutput(
    gaps=[CareGapItem(
        gap="overdue A1c", rule_id="A1C_MONITORING", evidence="diabetes",
        source=CareGapSource(
            organization="American Diabetes Association",
            title="Glycemic Targets", grade="B", year=2026,
            url="https://doi.org/10.2337/dc26-S006"))],
    confidence=0.9)

CODING = CodingOutput(
    codes=[CodeSuggestion(system="ICD-10", code="E11.9",
                          description="Type 2 diabetes without complications",
                          vocabulary_status="verified")],
    confidence=0.77, vocabulary_version="icd10cm-2026;hcpcs-2026")

ARTIFACTS = {"prior_auth": PRIOR_AUTH, "care_gap": CARE_GAP, "coding": CODING}


class StubAgent:
    """A real uvicorn server on an ephemeral port, torn down after the test."""

    def __init__(self, app: FastAPI):
        config = uvicorn.Config(app, host="127.0.0.1", port=0,
                                log_level="warning",
                                timeout_graceful_shutdown=1)
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "StubAgent":
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("stub agent server did not start")
            time.sleep(0.01)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=10)


def agent_app(agent: str, *, status: int = 200, body=None,
              delay: float = 0.0, marks: dict | None = None,
              received: dict | None = None) -> FastAPI:
    """One stub. `body=None` means return that agent's real artifact."""
    app = FastAPI()

    @app.post("/run")
    async def run(payload: dict):
        start = time.perf_counter()
        if received is not None:
            received[agent] = payload
        if delay:
            await asyncio.sleep(delay)
        if marks is not None:
            marks[agent] = (start, time.perf_counter())
        if status != 200:
            return JSONResponse(status_code=status,
                                content={"detail": "upstream model error"})
        return body if body is not None else ARTIFACTS[agent].model_dump()

    return app


def closed_port() -> int:
    """A port with nothing listening, for the connection-refused case."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def point_at(monkeypatch, **urls) -> None:
    for agent, url in urls.items():
        monkeypatch.setattr(settings, f"{agent}_url", url)


@pytest.fixture
def client():
    from services.orchestrator.app import app
    return TestClient(app)


def test_all_three_agents_answer_over_real_http(client, monkeypatch):
    received: dict = {}
    with StubAgent(agent_app("prior_auth", received=received)) as pa, \
         StubAgent(agent_app("care_gap", received=received)) as cg, \
         StubAgent(agent_app("coding", received=received)) as co:
        point_at(monkeypatch, prior_auth=pa.url, care_gap=cg.url,
                 coding=co.url)
        resp = client.post("/run", json=PAYLOAD)

    assert resp.status_code == 200
    out = resp.json()
    assert out["errors"] == {}
    assert out["prior_auth"]["items"][0]["item"] == "MRI lumbar spine"
    assert out["care_gap"]["gaps"][0]["rule_id"] == "A1C_MONITORING"
    assert out["coding"]["codes"][0]["code"] == "E11.9"
    # communication verified in the other direction too
    assert set(received) == {"prior_auth", "care_gap", "coding"}
    assert received["coding"]["soap"] == SOAP
```

- [ ] **Step 2: Run it**

```bash
.venv/Scripts/python.exe -m pytest tests/test_orchestrator_integration.py -q
```

Expected: `1 passed`. If it hangs, the stub startup guard raises after 10s
rather than blocking forever.

- [ ] **Step 3: Commit**

```bash
git add tests/test_orchestrator_integration.py
git commit -m "test(P2-6): orchestrator reaches three agents over real sockets"
```

---

### Task 9: Prove the fan-out is concurrent

**Files:**
- Modify: `tests/test_orchestrator_integration.py`

- [ ] **Step 1: Write the test**

```python
def test_the_three_agents_run_concurrently_not_in_sequence(client, monkeypatch):
    """Interval overlap, not a wall-clock threshold: a threshold is a flake
    waiting for a loaded CI runner. If all three were in flight at once, the
    last one to start did so before the first one finished."""
    marks: dict = {}
    delay = 0.4
    with StubAgent(agent_app("prior_auth", delay=delay, marks=marks)) as pa, \
         StubAgent(agent_app("care_gap", delay=delay, marks=marks)) as cg, \
         StubAgent(agent_app("coding", delay=delay, marks=marks)) as co:
        point_at(monkeypatch, prior_auth=pa.url, care_gap=cg.url,
                 coding=co.url)
        started = time.perf_counter()
        resp = client.post("/run", json=PAYLOAD)
        wall = time.perf_counter() - started

    assert resp.status_code == 200
    assert len(marks) == 3
    starts = [s for s, _ in marks.values()]
    ends = [e for _, e in marks.values()]
    assert max(starts) < min(ends), (
        f"agents did not overlap, so the fan-out is sequential: {marks}")
    # Corroborating, deliberately loose: sequential would be >= 1.2s.
    assert wall < delay * 3
```

- [ ] **Step 2: Run it**

Expected: `2 passed`. A failure here means the graph is running its nodes in
separate supersteps. Check that every edge is `START -> node`, not
`node -> node`.

- [ ] **Step 3: Commit**

```bash
git commit -am "test(P2-6): prove the fan-out overlaps rather than sequences"
```

---

### Task 10: Failure isolation, one test per failure class

**Files:**
- Modify: `tests/test_orchestrator_integration.py`

- [ ] **Step 1: Write the tests**

```python
def _assert_isolated(out: dict, failed: set[str]) -> None:
    """Whatever broke, every other agent still produced its artifact."""
    assert set(out["errors"]) == failed, out["errors"]
    for agent in {"prior_auth", "care_gap", "coding"} - failed:
        assert out[agent] is not None, f"{agent} was taken down with {failed}"
    for agent in failed:
        assert out[agent] is None
        assert out["errors"][agent], "an error entry must not be empty"


def test_a_502_from_one_agent_does_not_abort_the_others(client, monkeypatch):
    with StubAgent(agent_app("prior_auth")) as pa, \
         StubAgent(agent_app("care_gap")) as cg, \
         StubAgent(agent_app("coding", status=502)) as co:
        point_at(monkeypatch, prior_auth=pa.url, care_gap=cg.url,
                 coding=co.url)
        out = client.post("/run", json=PAYLOAD).json()
    _assert_isolated(out, {"coding"})
    assert "502" in out["errors"]["coding"]


def test_an_agent_that_is_not_listening_does_not_abort_the_others(
        client, monkeypatch):
    with StubAgent(agent_app("prior_auth")) as pa, \
         StubAgent(agent_app("care_gap")) as cg:
        point_at(monkeypatch, prior_auth=pa.url, care_gap=cg.url,
                 coding=f"http://127.0.0.1:{closed_port()}")
        out = client.post("/run", json=PAYLOAD).json()
    _assert_isolated(out, {"coding"})


def test_a_hung_agent_times_out_without_taking_the_others(client, monkeypatch):
    monkeypatch.setattr(settings, "agent_timeout_seconds", 0.3)
    with StubAgent(agent_app("prior_auth")) as pa, \
         StubAgent(agent_app("care_gap")) as cg, \
         StubAgent(agent_app("coding", delay=5.0)) as co:
        point_at(monkeypatch, prior_auth=pa.url, care_gap=cg.url,
                 coding=co.url)
        out = client.post("/run", json=PAYLOAD).json()
    _assert_isolated(out, {"coding"})
    assert "Timeout" in out["errors"]["coding"]
    assert "0.3s" in out["errors"]["coding"]


def test_a_schema_invalid_200_is_caught_at_the_node(client, monkeypatch):
    """The regression this exists for: raw agent JSON used to flow into
    PipelineResult, so one malformed 200 raised at response construction and
    destroyed all three artifacts."""
    with StubAgent(agent_app("prior_auth")) as pa, \
         StubAgent(agent_app("care_gap")) as cg, \
         StubAgent(agent_app("coding", body={"nonsense": True})) as co:
        point_at(monkeypatch, prior_auth=pa.url, care_gap=cg.url,
                 coding=co.url)
        resp = client.post("/run", json=PAYLOAD)
    assert resp.status_code == 200, "a malformed agent must not 500 the pipeline"
    _assert_isolated(resp.json(), {"coding"})
    assert "ValidationError" in resp.json()["errors"]["coding"]


def test_two_agents_failing_at_once_still_leaves_the_third(client, monkeypatch):
    """Without a reducer on the `errors` channel this raises
    InvalidUpdateError inside LangGraph and aborts the whole graph, including
    the healthy agent."""
    with StubAgent(agent_app("prior_auth", status=502)) as pa, \
         StubAgent(agent_app("care_gap")) as cg, \
         StubAgent(agent_app("coding", status=500)) as co:
        point_at(monkeypatch, prior_auth=pa.url, care_gap=cg.url,
                 coding=co.url)
        out = client.post("/run", json=PAYLOAD).json()
    _assert_isolated(out, {"prior_auth", "coding"})
    assert out["care_gap"]["gaps"][0]["rule_id"] == "A1C_MONITORING"
```

- [ ] **Step 2: Run the whole file**

Expected: `7 passed`

- [ ] **Step 3: Verify the reducer test actually detects its bug**

Temporarily change `errors` in `PipelineState` from
`Annotated[dict[str, str], _merge_errors]` to plain `dict[str, str]`, then:

```bash
.venv/Scripts/python.exe -m pytest tests/test_orchestrator_integration.py::test_two_agents_failing_at_once_still_leaves_the_third -q
```

Expected: FAIL. A test that passes with and without the reducer is not
testing the reducer. **Revert the change immediately afterwards** and re-run
to confirm it passes again.

- [ ] **Step 4: Commit**

```bash
git commit -am "test(P2-6): isolation across all five agent failure classes"
```

---

## Chunk 4: Verification and handoff

### Task 11: Full suite, lint, coverage

- [ ] **Step 1: Whole suite**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Expected: every test passing, with no new skips beyond the existing
`needs_data` guards.

- [ ] **Step 2: Lint**

```bash
.venv/Scripts/python.exe -m ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 3: Coverage of the changed module**

```bash
.venv/Scripts/python.exe -m pytest --cov=services.orchestrator --cov-report=term-missing -q
```

Record the number. Any uncovered line in `graph.py` is a failure path with no
test, so name it in the PR rather than leaving it silent.

- [ ] **Step 4: Confirm no em dashes were introduced**

The pattern is built with `printf` rather than typed literally, so this file
does not itself contain the character it is checking for and match its own
diff:

```bash
git diff main | grep -c "$(printf '\xe2\x80\x94')" || echo "0 em dashes"
```

Expected: `0 em dashes`

---

### Task 12: Live in-cluster verification (needs a human)

**This task cannot run unattended.** Docker Desktop is not running, starting
it is manual on this machine, and the run costs roughly $0.05 of real API
spend. Stop and hand back to the user before starting.

Per the P2-5 note in `docs/ROADMAP.md`: any secret rotation must trim
whitespace, or every outbound call dies as an opaque connection error.

- [ ] **Step 1: Bring the cluster up**

```bash
docker ps                                   # confirm the daemon is alive
~/bin/kind get clusters                     # expect: care-ops
kubectl get pods -n care-ops                # expect: 6 pods Running
```

If the cluster is gone, rebuild it per `k8s/README.md`.

- [ ] **Step 2: Rebuild and load the orchestrator image**

Required: the image currently in the cluster has no langgraph import and the
old pin.

```bash
docker build -f services/orchestrator/Dockerfile -t care-ops-orchestrator:latest .
~/bin/kind load docker-image care-ops-orchestrator:latest --name care-ops
kubectl rollout restart deployment/orchestrator -n care-ops
kubectl rollout status deployment/orchestrator -n care-ops --timeout=120s
```

- [ ] **Step 3: Seed an encounter and a note**

The care-gap agent writes to `agent_decisions`, which has foreign keys to both
`encounters` and `notes`. P2-5's smoke test died here, correctly.

```bash
kubectl exec -n care-ops deploy/db -- psql -U care_ops -d care_ops -c \
  "INSERT INTO encounters (external_ref, source_type) VALUES ('p2-6-verify', 'transcript') RETURNING id;"
```

Use the returned id as `<ENC>` below:

```bash
kubectl exec -n care-ops deploy/db -- psql -U care_ops -d care_ops -c \
  "INSERT INTO notes (encounter_id, soap, model) VALUES (<ENC>, '{\"subjective\":\"Patient has diabetes and reports low back pain for six weeks.\",\"objective\":\"BP 138/86.\",\"assessment\":\"Type 2 diabetes. Lumbar radiculopathy.\",\"plan\":\"Order MRI lumbar spine. Check A1c.\"}', 'manual-p2-6') RETURNING id;"
```

- [ ] **Step 4: The end-to-end run over Service DNS**

```bash
kubectl port-forward -n care-ops svc/orchestrator 8080:8000 &
curl -s -X POST http://localhost:8080/run -H 'Content-Type: application/json' \
  -d '{"encounter_id": <ENC>, "note_id": <NOTE>, "soap": {"subjective":"Patient has diabetes and reports low back pain for six weeks.","objective":"BP 138/86.","assessment":"Type 2 diabetes. Lumbar radiculopathy.","plan":"Order MRI lumbar spine. Check A1c."}}' | tee /tmp/p2-6-run.json
```

Expected: all three artifacts non-null, `errors` empty. Record the actual
response.

- [ ] **Step 5: Injected failure over Service DNS**

```bash
kubectl scale deployment/agent-coding -n care-ops --replicas=0
kubectl wait --for=delete pod -l app=agent-coding -n care-ops --timeout=60s
# re-run the same curl
kubectl scale deployment/agent-coding -n care-ops --replicas=1
```

Expected: `prior_auth` and `care_gap` still return artifacts, `errors.coding`
is a non-empty string naming the failing URL. This is the gate's third clause,
proved in the cluster rather than only in pytest.

- [ ] **Step 6: Record the evidence in `docs/ROADMAP.md`**

Append a `**DONE 2026-08-09.**` block under P2-6 in the same style as P2-4 and
P2-5: what ran, the exact observed output, and any bug found along the way.
State plainly which clauses are proved by pytest and which by the live run.

---

### Task 13: Finish the branch

- [ ] **Step 1: Update `docs/TECH-DESIGN.md` if it drifted**

Section 3.2 and line 28 already describe LangGraph fan-out. Correct anything
that is now wrong, in particular any claim about sequencing or timeouts.

- [ ] **Step 2: Use superpowers:finishing-a-development-branch**

Open the PR with the measured evidence in the body: the concurrency overlap
result, the five isolation cases, coverage, and the live run output.

- [ ] **Step 3: State the phase gate explicitly**

Per `CLAUDE.md`, do not start P2-7 until the user confirms P2-6's gate is met.
P2-7 drops to Sonnet 5 at `high` per `docs/MODEL-EFFORT-GUIDE.md` line 68, so
tell the user the exact `/model` and `/effort` commands and wait.

---

## Definition of done

| Gate clause | Proved by |
|---|---|
| `POST /run` fans out to all three agents | Task 8, plus Task 12 step 4 in-cluster |
| over in-cluster service DNS | Task 12 step 4, and `_agent_url` defaults |
| an integration test verifies inter-service communication | Task 8, real sockets, both directions |
| a single agent failure does not abort the other two | Task 10, five failure classes, plus Task 12 step 5 |
| (beyond the gate) the fan-out is actually parallel | Task 9, interval overlap |
