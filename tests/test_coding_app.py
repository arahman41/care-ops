from fastapi.testclient import TestClient
from services.agent_coding import app as coding_app
from services.agent_coding.agent import CodingError

client = TestClient(coding_app.app)
BODY = {"encounter_id": 1, "note_id": 1, "soap": {
    "subjective": "s", "objective": "o", "assessment": "a", "plan": "p"}}


def test_health():
    resp = client.get("/health")
    assert resp.json() == {"status": "ok", "service": "agent_coding"}


def test_run_happy_path(monkeypatch):
    from shared.schemas import CodingOutput
    monkeypatch.setattr(coding_app, "run", lambda inp: CodingOutput(
        codes=[], confidence=0.5, vocabulary_version="v"))
    resp = client.post("/run", json=BODY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_name"] == "coding"
    assert body["codes"] == []
    assert body["vocabulary_version"] == "v"
    # Computed fields must survive FastAPI's response_model serialization,
    # or the registry and P2-4 lose the counts.
    assert body["verified_count"] == 0
    assert body["not_found_count"] == 0


def test_run_returns_502_on_coding_error(monkeypatch):
    """P2-6 needs a clean per-agent failure signal to isolate one agent's
    failure from the other two."""
    def boom(inp):
        raise CodingError("model broke", "raw")
    monkeypatch.setattr(coding_app, "run", boom)
    resp = client.post("/run", json=BODY)
    assert resp.status_code == 502
    assert "model broke" in resp.json()["detail"]
