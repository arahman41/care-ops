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
