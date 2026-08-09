"""P2-6: the orchestrator graph's pure units.

Sockets, real HTTP, and failure isolation live in
tests/test_orchestrator_integration.py. This file covers the pieces that can
be tested without a server: the errors reducer, the error formatter, URL
resolution, and the compiled graph's shape.
"""
from __future__ import annotations

from shared.config import settings


def test_agent_timeout_has_a_default_justified_by_measurement():
    """60s is 3.9x the routed coding config's measured p95 of 15,517ms
    (P2-4 artifact coding_20260807T214249Z.json, 113 held-out notes)."""
    assert settings.agent_timeout_seconds == 60.0
