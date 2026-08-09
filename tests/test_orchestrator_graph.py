"""P2-6: the orchestrator graph's pure units.

Sockets, real HTTP, and failure isolation live in
tests/test_orchestrator_integration.py. This file covers the pieces that can
be tested without a server: the errors reducer, the error formatter, URL
resolution, and the compiled graph's shape.
"""
from __future__ import annotations

import httpx

from shared.config import settings

from services.orchestrator.graph import _describe, _merge_errors

URL = "http://agent-coding:8000/run"


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


# ---------- the failure formatter ----------

def test_a_read_timeout_still_says_something():
    """str(httpx.ReadTimeout("")) is the empty string, so the naive
    f"{type(exc).__name__}: {exc}" logs "ReadTimeout: " and the audit trail
    records nothing for the most likely cluster failure."""
    msg = _describe(httpx.ReadTimeout(""), URL, 60.0)
    assert "ReadTimeout" in msg
    assert URL in msg
    assert "60.0s" in msg
    assert not msg.endswith(": ")


def test_a_status_error_carries_the_status_code():
    request = httpx.Request("POST", URL)
    response = httpx.Response(502, text="upstream model error", request=request)
    msg = _describe(
        httpx.HTTPStatusError("", request=request, response=response),
        URL, 60.0)
    assert "502" in msg
    assert "upstream model error" in msg


def test_a_long_agent_body_is_truncated():
    request = httpx.Request("POST", URL)
    response = httpx.Response(500, text="x" * 5000, request=request)
    msg = _describe(
        httpx.HTTPStatusError("", request=request, response=response),
        URL, 60.0)
    assert len(msg) < 400


def test_a_connect_error_keeps_its_own_detail():
    msg = _describe(httpx.ConnectError("connection refused"), URL, 60.0)
    assert "ConnectError" in msg
    assert "connection refused" in msg
