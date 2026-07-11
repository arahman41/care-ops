"""Pydantic contracts shared across services.

These are the single source of truth for the data shapes that cross
service boundaries. Every agent returns a structured artifact, never
free text, and every artifact carries a confidence score in [0, 1].
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


# ---------- Layer 1: SOAP note ----------

class SoapNote(BaseModel):
    subjective: str
    objective: str
    assessment: str
    plan: str


class StructuredNote(BaseModel):
    encounter_id: int
    version: int = 1
    soap: SoapNote
    model: str
    model_effort: str | None = None


# ---------- Layer 2: agent inputs and outputs ----------

class AgentInput(BaseModel):
    encounter_id: int
    note_id: int
    soap: SoapNote


class PriorAuthItem(BaseModel):
    item: str                       # procedure or medication
    reason: str                     # why it commonly needs prior auth
    justification: str              # drafted snippet


class PriorAuthOutput(BaseModel):
    agent_name: Literal["prior_auth"] = "prior_auth"
    items: list[PriorAuthItem]
    confidence: float = Field(ge=0.0, le=1.0)


class CareGapItem(BaseModel):
    gap: str                        # e.g. overdue A1c screening
    rule_id: str                    # which rule fired
    evidence: str                   # text span or reason


class CareGapOutput(BaseModel):
    agent_name: Literal["care_gap"] = "care_gap"
    gaps: list[CareGapItem]
    confidence: float = Field(ge=0.0, le=1.0)


class CodeSuggestion(BaseModel):
    system: Literal["ICD-10", "CPT"]
    code: str
    description: str
    eligibility_flag: bool = False


class CodingOutput(BaseModel):
    agent_name: Literal["coding"] = "coding"
    codes: list[CodeSuggestion]
    confidence: float = Field(ge=0.0, le=1.0)


# ---------- Orchestrator response ----------

class PipelineResult(BaseModel):
    encounter_id: int
    note_id: int
    prior_auth: PriorAuthOutput | None = None
    care_gap: CareGapOutput | None = None
    coding: CodingOutput | None = None
    errors: dict[str, str] = Field(default_factory=dict)
