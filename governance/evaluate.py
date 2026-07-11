"""Re-score an agent against a held-out labeled set and store the result.

The held-out set is leak-free and never used to tune rules or prompts.
This is the same discipline used in ClinAIQA: measured numbers only.
"""
from __future__ import annotations

from sklearn.metrics import precision_recall_fscore_support, accuracy_score

from shared.db import get_conn


def score(y_true: list[int], y_pred: list[int]) -> dict:
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, average="binary", zero_division=0)
    return {"accuracy": accuracy_score(y_true, y_pred),
            "precision": precision, "recall": recall, "f1": f1}


def record_run(agent_name: str, model: str, window_label: str,
               dataset_ref: str, y_true: list[int], y_pred: list[int]) -> None:
    m = score(y_true, y_pred)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO eval_runs (agent_name, model, window_label, "
            "dataset_ref, n_examples, accuracy, f1, precision, recall) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (agent_name, model, window_label, dataset_ref, len(y_true),
             m["accuracy"], m["f1"], m["precision"], m["recall"]),
        )
