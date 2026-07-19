"""Coding/Eligibility Agent: suggest codes, flag possible eligibility issues.

Codes are SUGGESTIONS for human review, never confirmed codes. Whether a
code exists is decided here by lookup, not by the model: see shared/vocab.py.
"""
from __future__ import annotations

import time

from pydantic import ValidationError

from shared import vocab
from shared.llm import (
    MalformedJSONError, ROUTING, TruncatedResponseError, call, extract_json,
)
from shared.registry import log_decision
from shared.schemas import (
    AgentInput, CodeSuggestion, CodingOutput, ModelCodingPayload,
)

# Provisional. Task 13 of the P2-3 plan pins the real value from observed
# usage. The library default is 1500 and this agent emits the largest output
# of the three, so the cap is a live truncation risk rather than a formality.
_MAX_TOKENS = 4000

_SYSTEM = (
    "You suggest likely ICD-10 and CPT codes for a SOAP note and flag "
    "possible eligibility mismatches. Return only JSON: "
    '{"codes": [{"system": "ICD-10", "code": "", "description": "", '
    '"eligibility_flag": false}], "confidence": 0.0}. These are suggestions '
    "for human review, not confirmed codes. Confidence is calibrated in [0, 1]."
)


class CodingError(ValueError):
    """Raised when the model's response cannot be parsed into a CodingOutput.

    Carries a truncated preview of the raw output so a failure is
    diagnosable without dumping full model output into logs.
    """

    def __init__(self, reason: str, raw: str):
        preview = raw[:200] + ("..." if len(raw) > 200 else "")
        super().__init__(
            f"Coding parsing failed: {reason}. Raw output: {preview!r}")


def _enrich(payload: ModelCodingPayload) -> CodingOutput:
    """Turn what the model said into what the agent stands behind.

    The agent never branches on `system` itself; that decision lives in
    vocab.classify, so this file and P2-4 cannot drift apart.
    """
    codes = []
    for suggestion in payload.codes:
        # Stored in conventional dotted display form. Only the LOOKUP is
        # normalized, and classify does that internally. Do not pass this
        # through vocab.normalize: it strips the dot and would destroy the
        # display form this line exists to preserve.
        code = suggestion.code.strip().upper()
        flag = suggestion.eligibility_flag
        reason = suggestion.eligibility_reason
        if flag and not (reason or "").strip():
            # Degrade THIS suggestion only. Rejecting the whole payload
            # would discard every correctly validated code alongside it and
            # bias any rate computed over successful runs, while looking
            # from the outside like an ordinary parse failure.
            flag = False
            reason = None   # do not leave a blank reason on a cleared flag
        codes.append(CodeSuggestion(
            system=suggestion.system,
            code=code,
            description=suggestion.description,
            eligibility_flag=flag,
            eligibility_reason=reason,
            vocabulary_status=vocab.classify(suggestion.system, code),
        ))
    return CodingOutput(
        codes=codes,
        confidence=payload.confidence,
        vocabulary_version=vocab.VOCAB_VERSION,
    )


def run(inp: AgentInput) -> CodingOutput:
    model, effort = ROUTING["coding"]
    started = time.perf_counter()

    try:
        raw = call("coding", system=_SYSTEM, user=inp.soap.model_dump_json(),
                   max_tokens=_MAX_TOKENS)
    except TruncatedResponseError as exc:
        # raw="" because call() raises before returning any text. There is
        # no response to preview; the reason carries the diagnosis.
        raise CodingError(str(exc), "") from exc

    try:
        data = extract_json(raw)
    except MalformedJSONError as exc:
        raise CodingError(exc.reason, raw) from exc

    if not isinstance(data, dict):
        raise CodingError("JSON was not an object", raw)

    try:
        payload = ModelCodingPayload(**data)
    except ValidationError as exc:
        raise CodingError(
            f"did not match the ModelCodingPayload schema ({exc})",
            raw) from exc

    out = _enrich(payload)

    latency_ms = int((time.perf_counter() - started) * 1000)
    log_decision(
        encounter_id=inp.encounter_id, note_id=inp.note_id,
        agent_name="coding", model=model, effort=effort,
        input_ref=inp.soap.model_dump(), output=out.model_dump(),
        confidence=out.confidence, latency_ms=latency_ms,
    )
    return out
