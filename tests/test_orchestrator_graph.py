"""P2-6: the orchestrator graph's pure units.

Sockets, real HTTP, and failure isolation live in
tests/test_orchestrator_integration.py. This file covers the pieces that can
be tested without a server: the errors reducer, the error formatter, URL
resolution, and the compiled graph's shape.
"""
from __future__ import annotations

from shared.config import settings

from services.orchestrator.graph import _merge_errors


def test_agent_timeout_has_a_default_justified_by_measurement():
    """60s is 3.9x the routed coding config's measured p95 of 15,517ms
    (P2-4 artifact coding_20260807T214249Z.json, 113 held-out notes)."""
    assert settings.agent_timeout_seconds == 60.0


# ---------- the errors reducer ----------

def test_merge_errors_combines_two_failures():
    assert _merge_errors({"coding": "a"}, {"prior_auth": "b"}) == {
        "coding": "a", "prior_auth": "b"}


def test_merge_errors_does_not_mutate_either_input():
    """LangGraph holds the channel value across the superstep. A reducer that
    mutates its left argument corrupts state in a way that only shows up under
    concurrency."""
    left, right = {"coding": "a"}, {"prior_auth": "b"}
    _merge_errors(left, right)
    assert left == {"coding": "a"}
    assert right == {"prior_auth": "b"}


def test_merge_errors_starts_from_empty():
    assert _merge_errors({}, {"coding": "a"}) == {"coding": "a"}
