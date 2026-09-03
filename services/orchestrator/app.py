"""Orchestrator: takes a structured note, runs the agent graph."""
from __future__ import annotations

from fastapi import FastAPI

from governance.api import accuracy_trend, inventory_rows
from governance.transparency import build_report
from shared.registry import decisions_for_encounter
from shared.schemas import (AccuracyTrendRow, AgentInput, DecisionRecord,
                            InventoryRow, PipelineResult)
from services.orchestrator.graph import run_agents

app = FastAPI(title="Care Ops Copilot - Orchestrator")


@app.get("/health")
def health():
    return {"status": "ok", "service": "orchestrator"}


@app.post("/run", response_model=PipelineResult)
async def run(inp: AgentInput):
    # run_agents is a coroutine. Calling it without await returns a coroutine
    # object and every artifact silently becomes a validation error.
    out = await run_agents(inp.model_dump())
    return PipelineResult(
        encounter_id=inp.encounter_id,
        note_id=inp.note_id,
        prior_auth=out["prior_auth"],
        care_gap=out["care_gap"],
        coding=out["coding"],
        errors=out["errors"],
    )


@app.get("/encounters/{encounter_id}/decisions",
        response_model=list[DecisionRecord])
def get_decisions(encounter_id: int):
    # Synchronous, not async: one blocking psycopg call, no fan-out, unlike
    # /run. No existence check against `encounters`: an id with no
    # decisions yet and an id that was never created both legitimately
    # return []. See spec section 3 for why that is a stated
    # simplification rather than an oversight.
    return decisions_for_encounter(encounter_id)


# ---------- P3-5: governance read API ----------
#
# Three read endpoints, all synchronous (one blocking query each, no
# fan-out), all backed by real registry data, no mocked values. The
# dashboard (P4-1) reads these; none of them accepts a write.

@app.get("/governance/inventory", response_model=list[InventoryRow])
def get_inventory():
    return inventory_rows()


@app.get("/governance/accuracy-trend", response_model=list[AccuracyTrendRow])
def get_accuracy_trend(agent_name: str | None = None):
    # agent_name is optional so the dashboard can either pull one agent's
    # series or the whole trend table in one call; governance/api.py does
    # the filtering in SQL rather than this endpoint filtering in Python.
    return accuracy_trend(agent_name)


@app.get("/governance/transparency-report", response_model=list[dict])
def get_transparency_report():
    # No response schema narrower than dict: the report's keys are the nine
    # HTI-1 category names (governance/transparency.py::HTI1_CATEGORIES),
    # not a fixed set of Python identifiers, so a Pydantic model here would
    # either hardcode those strings as field names (illegal syntax) or alias
    # them, adding a translation layer the report itself does not have.
    return build_report()
