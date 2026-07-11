"""Turn a raw transcript into a structured SOAP note via Claude."""
from __future__ import annotations

import json

from pydantic import ValidationError

from shared.llm import call, ROUTING
from shared.schemas import SoapNote

_SYSTEM = (
    "You convert a doctor patient consultation transcript into a SOAP note. "
    "Return only JSON with keys subjective, objective, assessment, plan. "
    "Each value is a concise clinical paragraph grounded strictly in the "
    "transcript. Never invent, infer, or assume a symptom, vital sign, "
    "diagnosis, medication, or plan item that the transcript does not "
    "support. If a section has no content supported by the transcript, "
    "say so explicitly rather than fabricating detail."
)


class NoteStructuringError(ValueError):
    """Raised when the model's response cannot be parsed into a SoapNote.

    Carries a truncated preview of the raw output so a failure is
    diagnosable without dumping full model output into logs.
    """

    def __init__(self, reason: str, raw: str):
        preview = raw[:200] + ("..." if len(raw) > 200 else "")
        super().__init__(
            f"SOAP structuring failed: {reason}. Raw output: {preview!r}")


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw[3:]
        if raw[:4].lower() == "json":
            raw = raw[4:]
        raw = raw.removesuffix("```")
    return raw.strip()


def structure_note(transcript: str) -> tuple[SoapNote, str, str | None]:
    model, effort = ROUTING["structuring"]
    raw = call("structuring", system=_SYSTEM, user=transcript, max_tokens=1200)
    cleaned = _strip_code_fence(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise NoteStructuringError(f"not valid JSON ({exc})", raw) from exc

    if not isinstance(data, dict):
        raise NoteStructuringError("JSON was not an object", raw)

    try:
        note = SoapNote(**data)
    except ValidationError as exc:
        raise NoteStructuringError(
            f"did not match the SoapNote schema ({exc})", raw) from exc

    return note, model, effort
