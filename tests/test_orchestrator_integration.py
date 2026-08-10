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


def test_health_returns_ok(client):
    """Matches the convention in every other service's app test, and it is
    what the Kubernetes readiness and liveness probes call."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "orchestrator"}


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


# ---------- failure isolation, one test per failure class ----------

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
