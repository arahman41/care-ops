"""Orchestrator: takes a structured note, runs the agent graph."""
from __future__ import annotations

from fastapi import FastAPI

from shared.registry import decisions_for_encounter
from shared.schemas import AgentInput, DecisionRecord, PipelineResult
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
