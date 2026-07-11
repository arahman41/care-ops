"""Orchestrator: takes a structured note, runs the agent graph."""
from __future__ import annotations

from fastapi import FastAPI

from shared.schemas import AgentInput, PipelineResult
from services.orchestrator.graph import run_agents

app = FastAPI(title="Care Ops Copilot - Orchestrator")


@app.get("/health")
def health():
    return {"status": "ok", "service": "orchestrator"}


@app.post("/run", response_model=PipelineResult)
def run(inp: AgentInput):
    out = run_agents(inp.model_dump())
    return PipelineResult(
        encounter_id=inp.encounter_id,
        note_id=inp.note_id,
        prior_auth=out["prior_auth"],
        care_gap=out["care_gap"],
        coding=out["coding"],
        errors=out["errors"],
    )
