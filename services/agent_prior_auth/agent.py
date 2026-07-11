"""Prior-Auth Agent: flag items needing prior authorization, draft a snippet."""
from __future__ import annotations

import json
import time

from shared.llm import call, ROUTING
from shared.registry import log_decision
from shared.schemas import AgentInput, PriorAuthOutput

_SYSTEM = (
    "You review a SOAP note and identify procedures or medications that "
    "commonly require prior authorization. Return only JSON: "
    '{"items": [{"item": "", "reason": "", "justification": ""}], '
    '"confidence": 0.0}. Confidence is your calibrated certainty in [0, 1]. '
    "If nothing requires prior authorization, return an empty items list."
)


def run(inp: AgentInput) -> PriorAuthOutput:
    model, effort = ROUTING["prior_auth"]
    started = time.perf_counter()
    raw = call("prior_auth", system=_SYSTEM, user=inp.soap.model_dump_json())
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    out = PriorAuthOutput(**json.loads(raw))
    latency_ms = int((time.perf_counter() - started) * 1000)
    log_decision(
        encounter_id=inp.encounter_id, note_id=inp.note_id,
        agent_name="prior_auth", model=model, effort=effort,
        input_ref=inp.soap.model_dump(), output=out.model_dump(),
        confidence=out.confidence, latency_ms=latency_ms,
    )
    return out
