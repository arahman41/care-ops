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

import json
from pathlib import Path

from governance.drift import DriftVerdict, compare_structuring_windows
from shared.db import get_conn

ARTIFACT_DIR = Path(__file__).resolve().parent / "eval_artifacts"

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


def _same_model_windows(agent_name: str, model: str) -> list[dict]:
    """Every eval_runs row for THIS agent's THIS model, newest first.

    Filtering by model is what keeps coding's two P2-4 arms from being read
    as two time windows of one thing: model_inventory's coding row has
    model='claude-opus-4-8' (the routed arm), so this returns only that arm's
    row, never claude-sonnet-5's.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, window_label, dataset_ref, n_examples, accuracy, f1, "
            "precision, recall, metrics, created_at FROM eval_runs "
            "WHERE agent_name = %s AND model = %s ORDER BY created_at DESC",
            (agent_name, model))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _quantitative_summary(row: dict) -> str:
    """One line naming the real numbers this row measured.

    accuracy/f1/precision/recall are the paired-comparable family (P3-1); a
    row that has none of those (coding, care_gap, prior_auth: all NULL by the
    P3-1 guard) falls back to the metrics JSONB's own headline number, named
    explicitly rather than printed as a bare float.
    """
    parts = [f"{name}={row[name]:.6f}" for name in
             ("accuracy", "f1", "precision", "recall") if row[name] is not None]
    if parts:
        return (f"window {row['window_label']!r}, n={row['n_examples']}: " +
                ", ".join(parts))

    metrics = row["metrics"] or {}
    if "verified_rate" in metrics:
        return (f"window {row['window_label']!r}, n={row['n_examples']}: "
                f"verified_rate={metrics['verified_rate']:.2f}, "
                f"not_found_rate={metrics.get('not_found_rate', 'n/a')}")
    return f"window {row['window_label']!r}, n={row['n_examples']}: no scored metric"


def _performance_and_validation(agent_name: str, model: str) -> dict:
    windows = _same_model_windows(agent_name, model)
    if not windows:
        return {
            "Details and output of the DSI": "not yet measured",
            "Quantitative measures of performance": "not yet measured",
            "Updates and continued validation or fairness assessment schedule":
                "not yet measured",
        }

    latest = windows[0]
    same_dataset = [w for w in windows
                    if w["dataset_ref"] == latest["dataset_ref"]][:2]

    details = (f"{agent_name} on {model}, dataset {latest['dataset_ref']!r}, "
              f"measured {latest['created_at'].date().isoformat()}")
    performance = _quantitative_summary(latest)

    if len(same_dataset) < 2:
        validation = ("only one window filed for this dataset; no drift "
                      "comparison possible yet")
    elif agent_name != "note_structuring":
        # No paired-comparable metric exists for this agent (P3-1's
        # accuracy-family guard). Two same-model rows here would be unusual
        # today (coding's model filter already isolates one arm), but if it
        # ever happens, state that plainly rather than attempting a
        # comparison compare_structuring_windows was not built to make.
        validation = (f"{len(same_dataset)} windows filed, but {agent_name!r} "
                      f"has no paired-comparable accuracy metric to compare "
                      f"across them")
    else:
        validation = _drift_summary(same_dataset)

    return {
        "Details and output of the DSI": details,
        "Quantitative measures of performance": performance,
        "Updates and continued validation or fairness assessment schedule":
            validation,
    }


def _artifact_payload(row: dict) -> dict:
    name = (row["metrics"] or {}).get("provenance", {}).get("artifact")
    if not name:
        raise ValueError(
            f"eval_runs row {row['id']} has no provenance.artifact; it was "
            f"filed before P3-1 started recording it, or by a path that "
            f"never wrote one. Re-file it with scripts/refile_eval_run.py "
            f"before it can be drift-compared.")
    return json.loads((ARTIFACT_DIR / name).read_text(encoding="utf-8"))


def _drift_summary(same_dataset: list[dict]) -> str:
    """same_dataset[0] is NEWEST (the query is ORDER BY created_at DESC)."""
    newer, older = same_dataset[0], same_dataset[1]
    result = compare_structuring_windows(
        _artifact_payload(older), _artifact_payload(newer), replicates=2000)

    if result.delta is not None:
        header = (f"{result.verdict.value.upper()}: {older['window_label']!r} "
                  f"({older['created_at'].date().isoformat()}) vs "
                  f"{newer['window_label']!r} "
                  f"({newer['created_at'].date().isoformat()}), delta "
                  f"{result.delta:+.6f}")
    else:
        header = result.verdict.value.upper()

    if result.verdict is DriftVerdict.NOT_ATTRIBUTABLE:
        return header + ". " + " ".join(result.caveats)
    return header
