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
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from shared.config import settings
from shared.schemas import (CareGapItem, CareGapOutput, CareGapSource,
                            CodeSuggestion, CodingOutput, PriorAuthItem,
                            PriorAuthOutput)
from tests.live_server import LiveServer as StubAgent
from tests.live_server import point_at

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


# ---------- P3-5: the governance read API ----------
#
# inventory_rows, accuracy_trend, and build_report are monkeypatched here;
# their real queries are proven against a real database in
# tests/test_governance_api.py (the first two) and tests/test_transparency.py
# (the third). This is the HTTP surface only: status code, response shape,
# and that a query parameter actually reaches the underlying call.

def test_get_inventory_returns_the_registry_rows(client, monkeypatch):
    stub_rows = [{
        "id": 1, "agent_name": "coding", "model": "claude-opus-4-8",
        "version": "v1", "intended_use": "suggest codes for human review",
        "training_data_note": None, "known_limitations": None,
        "updated_at": "2026-09-02T00:00:00Z",
        "cautioned_out_of_scope_use": None, "fairness_process_note": None,
        "external_validation_note": None, "maintenance_schedule": None,
    }]
    monkeypatch.setattr("services.orchestrator.app.inventory_rows",
                        lambda: stub_rows)

    resp = client.get("/governance/inventory")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["agent_name"] == "coding"
    assert body[0]["model"] == "claude-opus-4-8"


def test_get_accuracy_trend_passes_the_agent_name_filter_through(
        client, monkeypatch):
    captured = {}

    def fake_trend(agent_name=None):
        captured["agent_name"] = agent_name
        return []

    monkeypatch.setattr("services.orchestrator.app.accuracy_trend", fake_trend)

    resp = client.get("/governance/accuracy-trend",
                      params={"agent_name": "coding"})

    assert resp.status_code == 200
    assert captured["agent_name"] == "coding"


def test_get_accuracy_trend_with_no_query_param_passes_none(
        client, monkeypatch):
    """None means "every agent", not "no agent": governance/api.py branches
    on this exact value to decide whether to add a WHERE clause."""
    captured = {}

    def fake_trend(agent_name=None):
        captured["agent_name"] = agent_name
        return []

    monkeypatch.setattr("services.orchestrator.app.accuracy_trend", fake_trend)

    resp = client.get("/governance/accuracy-trend")

    assert resp.status_code == 200
    assert captured["agent_name"] is None


def test_get_transparency_report_returns_build_reports_rows(
        client, monkeypatch):
    stub_report = [{"agent_name": "coding", "model": "claude-opus-4-8"}]
    monkeypatch.setattr("services.orchestrator.app.build_report",
                        lambda: stub_report)

    resp = client.get("/governance/transparency-report")

    assert resp.status_code == 200
    assert resp.json() == stub_report


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
