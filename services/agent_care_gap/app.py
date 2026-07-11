from __future__ import annotations

import time
from fastapi import FastAPI

from shared.registry import log_decision
from shared.schemas import AgentInput, CareGapOutput, CareGapItem
from services.agent_care_gap.rules import find_gaps

app = FastAPI(title="Care Ops Copilot - Care Gap Agent")


@app.get("/health")
def health():
    return {"status": "ok", "service": "agent_care_gap"}


@app.post("/run", response_model=CareGapOutput)
def run_endpoint(inp: AgentInput):
    started = time.perf_counter()
    blob = " ".join([inp.soap.subjective, inp.soap.objective,
                     inp.soap.assessment, inp.soap.plan])
    gaps = [CareGapItem(**g) for g in find_gaps(blob)]
    # Rules are deterministic, so confidence is fixed high when a rule fires.
    confidence = 0.9 if gaps else 1.0
    out = CareGapOutput(gaps=gaps, confidence=confidence)
    latency_ms = int((time.perf_counter() - started) * 1000)
    log_decision(
        encounter_id=inp.encounter_id, note_id=inp.note_id,
        agent_name="care_gap", model="rules-v1", effort=None,
        input_ref=inp.soap.model_dump(), output=out.model_dump(),
        confidence=confidence, latency_ms=latency_ms,
    )
    return out
