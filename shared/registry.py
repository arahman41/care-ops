"""Model registry logging. Every agent decision lands here."""
from __future__ import annotations

import json

from shared.db import get_conn


def log_decision(*, encounter_id: int, note_id: int, agent_name: str,
                 model: str, effort: str | None, input_ref: dict,
                 output: dict, confidence: float,
                 latency_ms: int | None) -> int:
    """Write one audit row. Returns the decision id."""
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO agent_decisions "
            "(encounter_id, note_id, agent_name, model, model_effort, "
            " input_ref, output, confidence, latency_ms) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (encounter_id, note_id, agent_name, model, effort,
             json.dumps(input_ref), json.dumps(output),
             confidence, latency_ms),
        ).fetchone()
        return row[0]


def decisions_for_encounter(encounter_id: int) -> list[dict]:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT agent_name, model, output, confidence, created_at "
            "FROM agent_decisions WHERE encounter_id = %s ORDER BY created_at",
            (encounter_id,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
