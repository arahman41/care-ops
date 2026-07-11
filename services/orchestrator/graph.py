"""LangGraph fan-out to the three agents, then collect their artifacts.

v1 routing is deterministic: every note visits all three agents in
parallel. Each agent call is independent so one failure does not abort
the others. Edges here are the seam where richer routing lands in v2.
"""
from __future__ import annotations

import httpx

from shared.config import settings

_AGENTS = {
    "prior_auth": f"{settings.prior_auth_url}/run",
    "care_gap": f"{settings.care_gap_url}/run",
    "coding": f"{settings.coding_url}/run",
}


def run_agents(payload: dict) -> dict:
    results: dict = {"prior_auth": None, "care_gap": None,
                     "coding": None, "errors": {}}
    with httpx.Client(timeout=60.0) as client:
        for name, url in _AGENTS.items():
            try:
                r = client.post(url, json=payload)
                r.raise_for_status()
                results[name] = r.json()
            except Exception as exc:  # isolate per-agent failure
                results["errors"][name] = str(exc)
    return results
