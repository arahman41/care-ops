"""P1-5: the intake endpoint itself, not just the pieces underneath it.

P1-3's done criteria (200/422/health) were verified live against a running
Postgres, by hand, once. That proves the service worked on 2026-07-11. It does
not stay true, because nothing re-checks it. These tests exercise the actual
FastAPI routes with TestClient, so a regression here fails in CI on every push
rather than needing to be rediscovered by hand.

structure_note, transcribe, and the two DB inserts are all mocked. The other
test files already prove those pieces work in isolation (test_structure.py,
test_transcribe.py, and P1-3's live verification for the DB layer); this file's
job is the wiring between them: does a request actually reach the right
function with the right arguments, and does a raised error become the right
status code.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.intake.app import app
from services.intake.structure import NoteStructuringError
from shared.schemas import SoapNote

SOAP = SoapNote(subjective="Cough for 3 days.", objective="Lungs clear.",
                assessment="URI.", plan="Rest and fluids.")


@pytest.fixture
def client():
    return TestClient(app)


def _mock_pipeline(monkeypatch, *, encounter_id=1, note_id=1):
    """The happy path: structuring succeeds, both inserts succeed."""
    monkeypatch.setattr("services.intake.app.structure_note",
                        lambda transcript: (SOAP, "stub-model", "high"))
    monkeypatch.setattr("services.intake.app.insert_encounter",
                        lambda external_ref, source_type: encounter_id)
    monkeypatch.setattr("services.intake.app.insert_note",
                        lambda encounter_id, soap, model, effort: note_id)


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "intake"}


def test_intake_with_a_transcript_returns_200_and_the_soap_note(
        client, monkeypatch):
    _mock_pipeline(monkeypatch, encounter_id=7, note_id=42)

    resp = client.post("/intake", json={"transcript": "Doctor: cough? "
                                                       "Patient: yes."})

    assert resp.status_code == 200
    body = resp.json()
    assert body["encounter_id"] == 7
    assert body["note_id"] == 42
    assert body["soap"] == SOAP.model_dump()
    assert body["model"] == "stub-model"
    assert body["effort"] == "high"


def test_intake_with_neither_transcript_nor_audio_is_422(client):
    resp = client.post("/intake", json={})
    assert resp.status_code == 422


def test_intake_with_a_whitespace_only_transcript_is_422(client, monkeypatch):
    # This request never reaches structure_note. If it does, the mock's
    # signature mismatch or an API call would be the tell; asserting 422 is
    # the direct check.
    monkeypatch.setattr("services.intake.app.structure_note",
                        lambda transcript: pytest.fail(
                            "structure_note must not be called for blank input"))

    resp = client.post("/intake", json={"transcript": "   "})
    assert resp.status_code == 422


def test_intake_with_audio_transcribes_then_structures(client, monkeypatch):
    """transcribe is imported into app.py's own namespace, so it is mocked there,
    the same as structure_note below, not at services.intake.transcribe."""
    monkeypatch.setattr("services.intake.app.transcribe",
                        lambda audio_path, model_size: "Doctor: cough? "
                                                       "Patient: yes.")
    _mock_pipeline(monkeypatch)

    resp = client.post("/intake", json={"audio_path": "/tmp/consult.wav"})

    assert resp.status_code == 200
    assert resp.json()["soap"] == SOAP.model_dump()


def test_a_structuring_failure_is_a_502_not_a_500(client, monkeypatch):
    """The model or the pipeline failing is a bad-gateway, not a bug in this
    service. A raw 500 here would hide which side actually broke."""
    def raise_structuring_error(transcript):
        raise NoteStructuringError("not valid JSON (test)", "garbage")

    monkeypatch.setattr("services.intake.app.structure_note",
                        raise_structuring_error)

    resp = client.post("/intake", json={"transcript": "some dialogue"})

    assert resp.status_code == 502
    assert "not valid JSON" in resp.json()["detail"]
