"""Intake service: audio or transcript in, structured SOAP note out."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.config import settings
from shared.db import insert_encounter, insert_note
from services.intake.structure import structure_note

app = FastAPI(title="Care Ops Copilot - Intake")


class IntakeRequest(BaseModel):
    transcript: str | None = None
    audio_path: str | None = None
    external_ref: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "intake"}


@app.post("/intake")
def intake(req: IntakeRequest):
    if not req.transcript and not req.audio_path:
        raise HTTPException(422, "Provide transcript or audio_path")

    if req.audio_path:
        from services.intake.transcribe import transcribe
        transcript = transcribe(req.audio_path, model_size=settings.whisper_model_size)
        source = "audio"
    else:
        transcript = req.transcript
        source = "transcript"

    if not transcript.strip():
        raise HTTPException(422, "Empty transcript")

    soap, model, effort = structure_note(transcript)
    encounter_id = insert_encounter(req.external_ref, source)
    note_id = insert_note(encounter_id, soap.model_dump(), model, effort)
    return {"encounter_id": encounter_id, "note_id": note_id,
            "soap": soap.model_dump(), "model": model, "effort": effort}
