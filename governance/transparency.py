"""P3-4: an ONC HTI-1 style transparency report over the model registry.

The 9 category names below are the source-attribute categories AHIMA's
summary of the HTI-1 final rule groups its 31 required attributes into (the
Federal Register page itself blocks automated fetching; this is the best
available primary-adjacent source, and this report claims HTI-1 "style," not
certification). Six are answered from model_inventory, seeded by
scripts/seed_model_inventory.py from language this project has already
committed elsewhere. Three (details/output, quantitative performance, and the
update/revalidation schedule) are answered LIVE from eval_runs and
governance.drift, never stored as text: a stored number goes stale the moment
a new window is filed, the way governance/pricing.json and the P1-4 cache key
both did, silently.

build_report() takes no arguments. It reads whatever the database currently
holds, so it is only as current as the last seed and the last eval_runs
write, and never any less current than that.
"""
from __future__ import annotations

from shared.db import get_conn

# Order matches the AHIMA summary of the HTI-1 source-attribute categories.
HTI1_CATEGORIES = (
    "Details and output of the DSI",
    "Purpose of the intervention",
    "Cautioned out-of-scope use of the intervention",
    "Intervention development details and input features",
    "Process used to ensure fairness in development of the intervention",
    "External validation process",
    "Quantitative measures of performance",
    "Ongoing maintenance of intervention implementation and use",
    "Updates and continued validation or fairness assessment schedule",
)

_INVENTORY_COLUMNS = (
    "agent_name", "model", "version", "intended_use", "training_data_note",
    "known_limitations", "cautioned_out_of_scope_use",
    "fairness_process_note", "external_validation_note",
    "maintenance_schedule",
)

# model_inventory column -> HTI-1 category it answers.
_STATIC_MAPPING = {
    "intended_use": "Purpose of the intervention",
    "training_data_note": "Intervention development details and input features",
    "known_limitations": "Cautioned out-of-scope use of the intervention",
    "cautioned_out_of_scope_use": "Cautioned out-of-scope use of the intervention",
    "fairness_process_note": "Process used to ensure fairness in development of the intervention",
    "external_validation_note": "External validation process",
    "maintenance_schedule": "Ongoing maintenance of intervention implementation and use",
}


def _inventory_rows() -> list[dict]:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT agent_name, model, version, intended_use, "
            "training_data_note, known_limitations, "
            "cautioned_out_of_scope_use, fairness_process_note, "
            "external_validation_note, maintenance_schedule "
            "FROM model_inventory ORDER BY agent_name")
        return [dict(zip(_INVENTORY_COLUMNS, row)) for row in cur.fetchall()]


def build_report() -> list[dict]:
    return [_report_row(inv) for inv in _inventory_rows()]


def _report_row(inv: dict) -> dict:
    row = {"agent_name": inv["agent_name"], "model": inv["model"]}

    for column, category in _STATIC_MAPPING.items():
        # known_limitations and cautioned_out_of_scope_use both target the
        # same category for agents where they read the same (coding,
        # prior_auth); the more specific one, written second, wins.
        row[category] = inv[column]

    row.update(_performance_and_validation(inv["agent_name"], inv["model"]))
    return row


def _performance_and_validation(agent_name: str, model: str) -> dict:
    # TODO(P3-4 Task 4): live join against eval_runs.
    return {
        "Details and output of the DSI": "not yet measured",
        "Quantitative measures of performance": "not yet measured",
        "Updates and continued validation or fairness assessment schedule":
            "not yet measured",
    }
