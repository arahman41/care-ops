"""LangGraph fan-out to the three agents, then collect their artifacts.

v1 routing is deterministic: every note visits all three agents, in one
superstep, so the pipeline's wall clock is the slowest agent rather than the
sum of all three. The edges here are the seam where richer routing lands in
v2.

Two properties are load-bearing and are easy to break by tidying:

1. `errors` carries a reducer. Three nodes can write it in the same superstep
   and LangGraph raises InvalidUpdateError on an unreduced concurrent write,
   which would abort the whole graph. Isolation depends on this.
2. Each node validates its own agent's response against that agent's own
   contract, inside the node. Validating later, at PipelineResult, lets one
   agent's malformed 200 destroy all three artifacts.
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from shared.config import settings
from shared.schemas import CareGapOutput, CodingOutput, PriorAuthOutput


def _merge_errors(left: dict[str, str],
                  right: dict[str, str]) -> dict[str, str]:
    """Reducer for the `errors` channel. Returns a new dict: mutating `left`
    would corrupt the channel value mid-superstep."""
    return {**left, **right}


def _agent_url(setting_name: str) -> str:
    """Resolved per call, never at import: a module-level constant freezes the
    cluster DNS name and makes the setting unoverridable."""
    return f"{getattr(settings, setting_name).rstrip('/')}/run"


_BODY_CHARS = 200


def _describe(exc: Exception, url: str, timeout: float) -> str:
    """One format for every failure entry, so no call site invents its own.

    Always names the exception class and the URL that failed, which is what
    makes a cluster DNS problem distinguishable from an agent bug. str() alone
    is not enough: str(httpx.ReadTimeout("")) is the empty string.
    """
    name = type(exc).__name__
    detail = str(exc).strip()
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:_BODY_CHARS].strip()
        detail = f"HTTP {exc.response.status_code} {body}".strip()
    elif isinstance(exc, httpx.TimeoutException) and not detail:
        detail = f"no response within {timeout}s"
    return f"{name} calling {url}: {detail}" if detail else f"{name} calling {url}"


class PipelineState(TypedDict):
    """`errors` is the only key with more than one possible writer, which is
    why it is the only one carrying a reducer. The three artifact keys have
    exactly one writer each, so LastValue is correct for them."""
    payload: dict
    prior_auth: dict | None
    care_gap: dict | None
    coding: dict | None
    errors: Annotated[dict[str, str], _merge_errors]


# (state key, settings attribute, the contract that agent must satisfy)
AGENTS: tuple[tuple[str, str, type[BaseModel]], ...] = (
    ("prior_auth", "prior_auth_url", PriorAuthOutput),
    ("care_gap", "care_gap_url", CareGapOutput),
    ("coding", "coding_url", CodingOutput),
)


def _make_node(agent: str, setting_name: str, schema: type[BaseModel]):
    """One code path for all three agents.

    Every failure class becomes an `errors` entry rather than an exception: a
    raise here aborts the graph and takes the other two agents with it. That
    covers connection refused, DNS failure, read timeout, non-2xx status, a
    body that is not JSON, and a 200 whose shape violates the contract.
    """
    async def node(state: PipelineState) -> dict[str, Any]:
        url = _agent_url(setting_name)
        timeout = settings.agent_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=state["payload"])
                response.raise_for_status()
                artifact = schema.model_validate(response.json())
        except Exception as exc:
            return {"errors": {agent: _describe(exc, url, timeout)}}
        return {agent: artifact.model_dump()}

    node.__name__ = f"call_{agent}"
    return node


def _build_graph():
    builder = StateGraph(PipelineState)
    for agent, setting_name, schema in AGENTS:
        node_name = f"call_{agent}"
        builder.add_node(node_name, _make_node(agent, setting_name, schema))
        builder.add_edge(START, node_name)      # fan out
        builder.add_edge(node_name, END)        # join
    return builder.compile()


# Compiled once at import. Safe because nodes resolve their URL and timeout
# per call, so nothing about the environment is baked in here.
_GRAPH = _build_graph()


async def run_agents(payload: dict) -> dict:
    """Run all three agents concurrently. Never raises for an agent failure:
    the caller reads `errors` to find out what broke."""
    initial: PipelineState = {
        "payload": payload,
        "prior_auth": None,
        "care_gap": None,
        "coding": None,
        "errors": {},
    }
    return await _GRAPH.ainvoke(initial)
