"""P3-5: read-only queries backing the governance HTTP API.

Two queries live here, both used by services/orchestrator/app.py's
/governance/* endpoints. The transparency report, the third P3-5 endpoint,
already has a builder (governance/transparency.py::build_report) and is
exposed directly rather than duplicated here.

inventory_rows() is a plain dump of model_inventory: every column, no field
mapping. This is a different consumer than transparency.py's HTI-1 category
mapping, which needs a specific column set for a specific report shape; the
dashboard's inventory table just wants the raw row.

accuracy_trend() is a plain dump of eval_runs, oldest first per agent, so a
client can plot it directly without re-sorting. It returns the accuracy
family AS STORED, including the NULLs the P3-1 guard writes for coding,
care_gap, and prior_auth (see governance/evaluate.py::UNSCOREABLE). Hiding
those NULLs behind a client-side default would recreate, one layer further
out in a chart, the exact failure that guard exists to prevent: a verified
rate or a rules pass rate rendered where a reader would read it as accuracy.
"""
from __future__ import annotations

from shared.db import get_conn

_INVENTORY_COLUMNS = (
    "id", "agent_name", "model", "version", "intended_use",
    "training_data_note", "known_limitations", "updated_at",
    "cautioned_out_of_scope_use", "fairness_process_note",
    "external_validation_note", "maintenance_schedule",
)

_TREND_COLUMNS = (
    "id", "agent_name", "model", "model_effort", "window_label",
    "dataset_ref", "n_examples", "accuracy", "f1", "precision", "recall",
    "metrics", "created_at",
)


def inventory_rows() -> list[dict]:
    """Every model_inventory row, ordered by agent then by version history."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT " + ", ".join(_INVENTORY_COLUMNS) +
            " FROM model_inventory ORDER BY agent_name, updated_at")
        return [dict(zip(_INVENTORY_COLUMNS, row)) for row in cur.fetchall()]


def accuracy_trend(agent_name: str | None = None) -> list[dict]:
    """Every eval_runs row, oldest first, optionally filtered to one agent.

    Ordered ASCENDING (oldest first) on purpose: a trend chart plots left to
    right in time, and re-sorting client-side is one more place the order
    could be gotten backwards, the same class of bug P3-1's created_at
    redefinition (db/schema.sql) exists to prevent.
    """
    query = "SELECT " + ", ".join(_TREND_COLUMNS) + " FROM eval_runs"
    params: tuple = ()
    if agent_name is not None:
        query += " WHERE agent_name = %s"
        params = (agent_name,)
    query += " ORDER BY agent_name, created_at"

    with get_conn() as conn:
        cur = conn.execute(query, params)
        return [dict(zip(_TREND_COLUMNS, row)) for row in cur.fetchall()]
