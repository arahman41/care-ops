"""Coding/Eligibility Agent: suggest ICD-10/CPT codes, flag mismatches.

This is the hardest component. Default routing is Sonnet 5 at xhigh
effort. Benchmark Opus 4.8 at high on the held-out coding set and keep
the winner. Never present a code as confirmed; these are suggestions.
"""
from __future__ import annotations

import json
import time

from shared.llm import call, ROUTING
from shared.registry import log_decision
from shared.schemas import AgentInput, CodingOutput

_SYSTEM = (
    "You suggest likely ICD-10 and CPT codes for a SOAP note and flag "
    "possible eligibility mismatches. Return only JSON: "
    '{"codes": [{"system": "ICD-10", "code": "", "description": "", '
    '"eligibility_flag": false}], "confidence": 0.0}. These are suggestions '
    "for human review, not confirmed codes. Confidence is calibrated in [0, 1]."
)


def run(inp: AgentInput) -> CodingOutput:
    model, effort = ROUTING["coding"]
    started = time.perf_counter()
    raw = call("coding", system=_SYSTEM, user=inp.soap.model_dump_json())
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    out = CodingOutput(**json.loads(raw))
    latency_ms = int((time.perf_counter() - started) * 1000)
    log_decision(
        encounter_id=inp.encounter_id, note_id=inp.note_id,
        agent_name="coding", model=model, effort=effort,
        input_ref=inp.soap.model_dump(), output=out.model_dump(),
        confidence=out.confidence, latency_ms=latency_ms,
    )
    return out
