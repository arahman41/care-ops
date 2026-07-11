"""Drift detection over agent accuracy and confidence across windows.

Uses Evidently to compare a reference window against a current window.
The controlled test in tests/ injects an accuracy drop and asserts that
this function flags it, which is the drift-sensitivity success metric.
"""
from __future__ import annotations

import pandas as pd
from evidently.report import Report
from evidently.metric_preset import DataDriftPreset


def detect_drift(reference: pd.DataFrame, current: pd.DataFrame) -> dict:
    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=reference, current_data=current)
    payload = report.as_dict()
    drifted = _extract_dataset_drift(payload)
    return {"drift_detected": drifted, "raw": payload}


def _extract_dataset_drift(payload: dict) -> bool:
    # Evidently nests the dataset-level flag; walk defensively.
    for metric in payload.get("metrics", []):
        value = metric.get("value")
        if isinstance(value, dict) and value.get("dataset_drift") is True:
            return True
    return False
