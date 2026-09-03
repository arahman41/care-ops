"""P4-2: one test, transcript in, three logged agent decisions out.

Real end to end, not a wiring test: a real Postgres, the real intake app
writing a real encounter and note, the real orchestrator fanning out over
real HTTP to the three REAL agent apps (not stubs), care_gap's real rules
engine, and all three agents' own real log_decision calls landing in a real
agent_decisions table. The only two things mocked are the two calls that
would otherwise spend real money and make the test nondeterministic:
services.intake.structure.structure_note's underlying model call, and each
of prior_auth's and coding's shared.llm.call. Both are mocked at the
narrowest point that still runs everything downstream for real: parsing,
schema validation, vocabulary classification, and the registry write.
care_gap needs no such mock; its "model" is a deterministic rules engine
(see services/agent_care_gap/rules.py), so it runs entirely for real.

Guarded by needs_db and self-contained (own fixtures, own cleanup),
matching every other needs_db test in this repo: CI's Postgres has only
db/schema.sql applied, nothing seeded.
"""
from __future__ import annotations

import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from shared.config import settings
from shared.db import get_conn
from shared.schemas import SoapNote
from tests.live_server import LiveServer, point_at


def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="no reachable Postgres")

TRANSCRIPT = (
    "Doctor: What brings you in today? "
    "Patient: I've had a cough and mild fever for three days. "
    "Doctor: Lungs sound clear on exam, no wheezing. "
    "Assessment: likely viral upper respiratory infection. "
    "Plan: rest, fluids, follow up if symptoms worsen past a week."
)

STRUCTURED = SoapNote(
    subjective="Cough and mild fever for three days.",
    objective="Lungs clear on exam, no wheezing.",
    assessment="Likely viral upper respiratory infection.",
    plan="Rest, fluids, follow up if symptoms worsen past a week.")

PRIOR_AUTH_JSON = json.dumps({
    "items": [],
    "confidence": 0.6,
})

CODING_JSON = json.dumps({
    "codes": [{
        "system": "ICD-10", "code": "J06.9",
        "description": "Acute upper respiratory infection, unspecified",
        "eligibility_flag": False, "eligibility_reason": None,
    }],
    "confidence": 0.7,
})


@needs_db
def test_transcript_in_to_three_logged_decisions(monkeypatch):
    # ---- Layer 1: intake, real DB write, structuring mocked ----
    monkeypatch.setattr("services.intake.app.structure_note",
                        lambda transcript: (STRUCTURED, "stub-model", "high"))
    from services.intake.app import app as intake_app
    intake_resp = TestClient(intake_app).post(
        "/intake", json={"transcript": TRANSCRIPT, "external_ref": "p4-2-e2e"})
    assert intake_resp.status_code == 200
    body = intake_resp.json()
    encounter_id, note_id = body["encounter_id"], body["note_id"]

    try:
        # ---- Layer 2: three REAL agent apps, only their model calls mocked ----
        monkeypatch.setattr("services.agent_prior_auth.agent.call",
                            lambda *a, **k: PRIOR_AUTH_JSON)
        monkeypatch.setattr("services.agent_coding.agent.call",
                            lambda *a, **k: CODING_JSON)

        from services.agent_care_gap.app import app as care_gap_app
        from services.agent_coding.app import app as coding_app
        from services.agent_prior_auth.app import app as prior_auth_app
        from services.orchestrator.app import app as orchestrator_app

        with LiveServer(prior_auth_app) as pa, \
             LiveServer(care_gap_app) as cg, \
             LiveServer(coding_app) as co:
            point_at(monkeypatch, prior_auth=pa.url, care_gap=cg.url,
                    coding=co.url)

            run_resp = TestClient(orchestrator_app).post("/run", json={
                "encounter_id": encounter_id, "note_id": note_id,
                "soap": STRUCTURED.model_dump(),
            })

        assert run_resp.status_code == 200
        result = run_resp.json()
        assert result["errors"] == {}, (
            f"an agent failed rather than logging a decision: {result['errors']}")

        # ---- Layer 3: the registry, read back for real ----
        decisions_resp = TestClient(orchestrator_app).get(
            f"/encounters/{encounter_id}/decisions")
        assert decisions_resp.status_code == 200
        decisions = decisions_resp.json()

        agent_names = {d["agent_name"] for d in decisions}
        assert agent_names == {"prior_auth", "care_gap", "coding"}, (
            "expected exactly the three agents to have logged a decision, "
            f"got {agent_names}")
        for d in decisions:
            assert d["note_id"] == note_id
            assert 0.0 <= d["confidence"] <= 1.0
            assert d["output"], "an empty output would mean nothing was logged"
    finally:
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM agent_decisions WHERE encounter_id = %s",
                (encounter_id,))
            conn.execute("DELETE FROM notes WHERE encounter_id = %s",
                        (encounter_id,))
            conn.execute("DELETE FROM encounters WHERE id = %s",
                        (encounter_id,))
