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

import httpx

from shared.config import settings


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
