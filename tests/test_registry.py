"""P2-7: shared/registry.py against a real database.

Guarded by needs_db, mirroring tests/test_heldout_guard.py's needs_data
pattern: local dev has no standing Postgres (see the env-quirks project
note), CI's postgres:16 service always does.

log_decision and decisions_for_encounter are exercised against a real
Postgres rather than mocked, because the bug this task fixes (a SELECT
silently dropping columns) is exactly the class of bug a mocked connection
cannot catch.
"""
from __future__ import annotations

import psycopg
import pytest

from shared.config import settings
from shared.db import get_conn, insert_encounter, insert_note
from shared.registry import decisions_for_encounter, log_decision


def _db_reachable() -> bool:
    """A bounded timeout matters here specifically: an unreachable host must
    fail fast rather than hang collection on the OS's default TCP timeout."""
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(),
                              reason="no reachable Postgres")


@pytest.fixture
def encounter_and_note():
    encounter_id = insert_encounter("p2-7-test", "transcript")
    note_id = insert_note(encounter_id, {"subjective": "s", "objective": "o",
                                         "assessment": "a", "plan": "p"},
                          "test-model", None)
    yield encounter_id, note_id
    with get_conn() as conn:
        conn.execute("DELETE FROM agent_decisions WHERE encounter_id = %s",
                     (encounter_id,))
        conn.execute("DELETE FROM notes WHERE id = %s", (note_id,))
        conn.execute("DELETE FROM encounters WHERE id = %s", (encounter_id,))


@needs_db
def test_a_logged_decision_round_trips_every_field(encounter_and_note):
    encounter_id, note_id = encounter_and_note
    log_decision(
        encounter_id=encounter_id, note_id=note_id, agent_name="prior_auth",
        model="claude-sonnet-5", effort="high",
        input_ref={"subjective": "s"}, output={"items": []},
        confidence=0.75, latency_ms=4284,
    )

    rows = decisions_for_encounter(encounter_id)

    assert len(rows) == 1
    row = rows[0]
    assert row["agent_name"] == "prior_auth"
    assert row["note_id"] == note_id
    assert row["model"] == "claude-sonnet-5"
    assert row["model_effort"] == "high"
    assert row["input_ref"] == {"subjective": "s"}
    assert row["output"] == {"items": []}
    assert row["confidence"] == 0.75
    assert row["latency_ms"] == 4284
    assert row["created_at"] is not None


@needs_db
def test_the_query_returns_every_decision_for_the_encounter_in_order(
        encounter_and_note):
    encounter_id, note_id = encounter_and_note
    for agent, model in (("care_gap", "rules-v1"),
                         ("prior_auth", "claude-sonnet-5"),
                         ("coding", "claude-opus-4-8")):
        log_decision(encounter_id=encounter_id, note_id=note_id,
                    agent_name=agent, model=model, effort=None,
                    input_ref={}, output={}, confidence=0.5, latency_ms=1)

    rows = decisions_for_encounter(encounter_id)

    assert [r["agent_name"] for r in rows] == ["care_gap", "prior_auth",
                                               "coding"]


@needs_db
def test_an_encounter_with_no_decisions_returns_an_empty_list(
        encounter_and_note):
    encounter_id, _ = encounter_and_note
    assert decisions_for_encounter(encounter_id) == []
