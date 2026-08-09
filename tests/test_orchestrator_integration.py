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
