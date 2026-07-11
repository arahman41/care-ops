"""Turn a raw transcript into a structured SOAP note via Claude."""
from __future__ import annotations

import json

from shared.llm import call, ROUTING
from shared.schemas import SoapNote

_SYSTEM = (
    "You convert a doctor patient consultation transcript into a SOAP note. "
    "Return only JSON with keys subjective, objective, assessment, plan. "
    "Each value is a concise clinical paragraph. Do not invent findings that "
    "are not supported by the transcript."
)


def structure_note(transcript: str) -> tuple[SoapNote, str, str | None]:
    model, effort = ROUTING["structuring"]
    raw = call("structuring", system=_SYSTEM, user=transcript, max_tokens=1200)
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(raw)
    return SoapNote(**data), model, effort
