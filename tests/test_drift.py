"""Drift sensitivity: an injected accuracy drop must be flagged.

This is the controlled test behind the drift-detection success metric.
"""
import numpy as np
import pandas as pd

from governance.drift import detect_drift


def test_injected_shift_is_flagged():
    rng = np.random.default_rng(0)
    reference = pd.DataFrame({"confidence": rng.normal(0.9, 0.02, 500)})
    # Current window: confidence collapses, simulating a degraded agent.
    current = pd.DataFrame({"confidence": rng.normal(0.55, 0.05, 500)})
    result = detect_drift(reference, current)
    assert result["drift_detected"] is True
