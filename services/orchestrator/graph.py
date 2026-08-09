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


def _merge_errors(left: dict[str, str],
                  right: dict[str, str]) -> dict[str, str]:
    """Reducer for the `errors` channel. Returns a new dict: mutating `left`
    would corrupt the channel value mid-superstep."""
    return {**left, **right}
