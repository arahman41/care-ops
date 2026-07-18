"""P2-2: the care gap endpoint's wiring. The rules engine's own correctness
is covered by tests/test_care_gap_rules.py. This file's job is the endpoint:
is the confidence honest, and does a rule's citation survive the trip out?

log_decision is stubbed so these stay unit tests. The real registry write is
exercised by hand in the plan's live-verification task.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.agent_care_gap.app import RULE_MATCH_CONFIDENCE, app

SOAP = {"subjective": "s", "objective": "o", "assessment": "a", "plan": "p"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_registry_writes(monkeypatch):
    monkeypatch.setattr("services.agent_care_gap.app.log_decision",
                        lambda **kwargs: None)


def _post(client, subjective: str):
    resp = client.post("/run", json={
        "encounter_id": 1, "note_id": 1,
        "soap": {**SOAP, "subjective": subjective},
    })
    assert resp.status_code == 200
    return resp.json()


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "agent_care_gap"}


def test_a_clean_note_does_not_claim_certainty(client):
    """A keyword scan cannot be certain a note has no gaps, so the no-gap
    case must not report 1.0."""
    out = _post(client, "Patient here for a routine ankle check.")
    assert out["gaps"] == []
    assert out["confidence"] == RULE_MATCH_CONFIDENCE
    assert out["confidence"] < 1.0


def test_a_fired_rule_reports_the_same_fixed_confidence(client):
    out = _post(client, "Patient has diabetes.")
    assert out["gaps"]
    assert out["confidence"] == RULE_MATCH_CONFIDENCE


def test_a_fired_gap_carries_its_citation_through_the_endpoint(client):
    out = _post(client, "Patient has diabetes.")
    gap = next(g for g in out["gaps"] if g["rule_id"] == "A1C_MONITORING")
    assert gap["source"]["url"] == "https://doi.org/10.2337/dc26-S006"
