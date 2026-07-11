"""Generate an ONC HTI-1 style transparency report from registry data."""
from __future__ import annotations

from shared.db import get_conn

FIELDS = ["agent_name", "model", "version", "intended_use",
          "training_data_note", "known_limitations"]


def build_report() -> list[dict]:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT agent_name, model, version, intended_use, "
            "training_data_note, known_limitations FROM model_inventory "
            "ORDER BY agent_name")
        return [dict(zip(FIELDS, row)) for row in cur.fetchall()]
