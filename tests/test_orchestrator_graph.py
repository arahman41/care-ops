"""P2-6: the orchestrator graph's pure units.

Sockets, real HTTP, and failure isolation live in
tests/test_orchestrator_integration.py. This file covers the pieces that can
be tested without a server: the errors reducer, the error formatter, URL
resolution, and the compiled graph's shape.
"""
from __future__ import annotations

import httpx

from shared.config import settings

from services.orchestrator.graph import (AGENTS, PipelineState, _GRAPH,
                                         _agent_url, _describe, _merge_errors)

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


# ---------- URL resolution ----------

def test_agent_url_defaults_to_the_kubernetes_service_dns_name():
    assert _agent_url("coding_url") == "http://agent-coding:8000/run"


def test_agent_url_is_read_per_call_not_frozen_at_import(monkeypatch):
    """The integration test retargets these at localhost stubs. If the URL is
    computed at import time, that silently keeps hitting the cluster names."""
    monkeypatch.setattr(settings, "coding_url", "http://127.0.0.1:9999")
    assert _agent_url("coding_url") == "http://127.0.0.1:9999/run"


def test_agent_url_tolerates_a_trailing_slash(monkeypatch):
    monkeypatch.setattr(settings, "coding_url", "http://127.0.0.1:9999/")
    assert _agent_url("coding_url") == "http://127.0.0.1:9999/run"


# ---------- the compiled graph's shape ----------

def test_the_graph_has_exactly_one_node_per_agent():
    real = {n for n in _GRAPH.nodes if not n.startswith("__")}
    assert real == {"call_prior_auth", "call_care_gap", "call_coding"}


def test_every_node_name_differs_from_the_state_key_it_writes():
    """langgraph 0.2.60 raised ValueError on a collision. 1.2.10 permits it.
    The prefix is kept as a convention: a node is an action, a state key is an
    artifact."""
    keys = set(PipelineState.__annotations__)
    assert {n for n in _GRAPH.nodes if not n.startswith("__")}.isdisjoint(keys)


def test_state_carries_a_key_for_every_agent_plus_errors():
    assert set(PipelineState.__annotations__) == {
        "payload", "prior_auth", "care_gap", "coding", "errors"}


def test_every_agent_is_wired_to_its_own_schema():
    assert {a[0] for a in AGENTS} == {"prior_auth", "care_gap", "coding"}
    assert [a[2].__name__ for a in AGENTS] == [
        "PriorAuthOutput", "CareGapOutput", "CodingOutput"]
