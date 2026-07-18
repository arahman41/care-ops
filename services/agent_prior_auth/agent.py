"""Prior-Auth Agent: flag items needing prior authorization, draft a snippet."""
from __future__ import annotations

import time

from pydantic import ValidationError

from shared.llm import MalformedJSONError, call, extract_json, ROUTING
from shared.registry import log_decision
from shared.schemas import AgentInput, PriorAuthOutput

_SYSTEM = (
    "You review a SOAP note and identify procedures or medications that "
    "commonly require prior authorization. Return only JSON: "
    '{"items": [{"item": "", "reason": "", "justification": ""}], '
    '"confidence": 0.0}. Confidence is your calibrated certainty in [0, 1]. '
    "If nothing requires prior authorization, return an empty items list."
)


class PriorAuthError(ValueError):
    """Raised when the model's response cannot be parsed into a PriorAuthOutput.

    Carries a truncated preview of the raw output so a failure is
    diagnosable without dumping full model output into logs.
    """

    def __init__(self, reason: str, raw: str):
        preview = raw[:200] + ("..." if len(raw) > 200 else "")
        super().__init__(
            f"Prior-auth parsing failed: {reason}. Raw output: {preview!r}")


def run(inp: AgentInput) -> PriorAuthOutput:
    model, effort = ROUTING["prior_auth"]
    started = time.perf_counter()
    raw = call("prior_auth", system=_SYSTEM, user=inp.soap.model_dump_json())

    try:
        data = extract_json(raw)
    except MalformedJSONError as exc:
        raise PriorAuthError(exc.reason, raw) from exc

    if not isinstance(data, dict):
        raise PriorAuthError("JSON was not an object", raw)

    try:
        out = PriorAuthOutput(**data)
    except ValidationError as exc:
        raise PriorAuthError(
            f"did not match the PriorAuthOutput schema ({exc})", raw) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    log_decision(
        encounter_id=inp.encounter_id, note_id=inp.note_id,
        agent_name="prior_auth", model=model, effort=effort,
        input_ref=inp.soap.model_dump(), output=out.model_dump(),
        confidence=out.confidence, latency_ms=latency_ms,
    )
    return out
