"""Turn a raw transcript into a structured SOAP note via Claude."""
from __future__ import annotations

from pydantic import ValidationError

from shared.llm import MalformedJSONError, call, extract_json, ROUTING
from shared.schemas import SoapNote

# Public because the P1-4 eval harness hashes it into its generation cache
# key: editing this prompt must invalidate every cached SOAP note, or the
# harness would score old output against a new prompt.
SYSTEM_PROMPT = (
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


# The longest held-out reference note is ~1350 tokens, and a generated SOAP
# note plus its JSON scaffolding plus high-effort reasoning runs well past
# that. The original 1200 truncated real encounters mid-string (caught by the
# P1-4 harness on ACI-Bench D2N101, a 4019-character note). Unused output
# tokens are never billed, so a generous ceiling is free insurance against an
# entire class of failure.
MAX_TOKENS = 8000


# Structuring is a reasoning call, so it samples, and a sampled response is
# occasionally not valid JSON: one encounter in the 120-note held-out run
# (ACI-Bench D2N150) came back with an unescaped quote mid-string. Nothing
# retried, so that single bad sample aborted the whole eval. Re-calling the
# same transcript parsed cleanly, which is what makes this a sampling defect
# and not a prompt defect, and what makes resampling the right answer.
#
# The line, which tests/test_structure.py pins: resample the SERIALIZATION,
# never the CONTENT. Only unparseable JSON is retried. A response that parses
# but does not match the schema is the model answering the wrong question, and
# rerolling that would be shopping for a note we like, which is how an accuracy
# number quietly stops measuring the system it claims to measure.
#
# Structured output via tool use would make invalid JSON impossible rather than
# merely rare, and is the better fix. It changes the shape of the generation
# call, so it belongs with the intake hardening work and a fresh eval run, not
# in the middle of scoring one.
MAX_JSON_ATTEMPTS = 3


def structure_note(transcript: str) -> tuple[SoapNote, str, str | None]:
    model, effort = ROUTING["structuring"]

    for attempt in range(1, MAX_JSON_ATTEMPTS + 1):
        raw = call("structuring", system=SYSTEM_PROMPT, user=transcript,
                   max_tokens=MAX_TOKENS)
        try:
            data = extract_json(raw)
            break
        except MalformedJSONError as exc:
            if attempt == MAX_JSON_ATTEMPTS:
                raise NoteStructuringError(
                    f"{exc.reason} after {MAX_JSON_ATTEMPTS} attempts",
                    raw) from exc

    if not isinstance(data, dict):
        raise NoteStructuringError("JSON was not an object", raw)

    try:
        note = SoapNote(**data)
    except ValidationError as exc:
        raise NoteStructuringError(
            f"did not match the SoapNote schema ({exc})", raw) from exc

    return note, model, effort
