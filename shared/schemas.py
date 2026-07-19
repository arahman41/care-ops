"""Pydantic contracts shared across services.

These are the single source of truth for the data shapes that cross
service boundaries. Every agent returns a structured artifact, never
free text, and every artifact carries a confidence score in [0, 1].
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, computed_field


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


class CareGapSource(BaseModel):
    """The published guideline a care gap rule implements.

    Verified against primary sources on the date recorded in
    services/agent_care_gap/rules.py::CITATIONS_VERIFIED_ON.
    """
    organization: str               # e.g. "U.S. Preventive Services Task Force"
    title: str                      # guideline or chapter title, as published
    grade: str | None = None        # USPSTF A/B/C/I, ADA A/B/C/E; None if ungraded
    year: int
    url: str


class CareGapItem(BaseModel):
    gap: str                        # e.g. overdue A1c screening
    rule_id: str                    # which rule fired
    evidence: str                   # text span or reason
    source: CareGapSource           # the guideline this rule implements


class CareGapOutput(BaseModel):
    agent_name: Literal["care_gap"] = "care_gap"
    gaps: list[CareGapItem]
    confidence: float = Field(ge=0.0, le=1.0)


class ModelCodeSuggestion(BaseModel):
    """One code as the MODEL is allowed to state it.

    Deliberately carries no vocabulary_status. Model output is parsed into
    this, never into CodeSuggestion, so a model emitting
    vocabulary_status="verified" on a fabricated code has the key silently
    dropped by pydantic's extra="ignore" default. The model cannot certify
    its own hallucinations because it has no channel to make the claim.

    eligibility_flag means: this code is commonly subject to payer coverage
    or medical-necessity review, OR the note's documentation may not
    support it. Both are assessable from a note alone. Whether a specific
    patient's plan covers a service is NOT assessable here, and this agent,
    which receives no payer, plan, or benefits data, does not claim to
    answer it. When the flag is true, eligibility_reason is required; the
    agent degrades an unsubstantiated flag rather than rejecting the whole
    payload (see services/agent_coding/agent.py::_enrich).
    """
    system: Literal["ICD-10", "CPT", "HCPCS"]
    code: str
    description: str
    eligibility_flag: bool = False
    eligibility_reason: str | None = None


class ModelCodingPayload(BaseModel):
    """The whole model response, before the agent enriches it."""
    codes: list[ModelCodeSuggestion]
    confidence: float = Field(ge=0.0, le=1.0)


class CodeSuggestion(ModelCodeSuggestion):
    """One code as the AGENT returns it, with the status it computed.

    vocabulary_status is set by shared/vocab.py::classify, never by the
    model. "unchecked" means the code both looks like CPT and was declared
    CPT, CPT being the one system whose vocabulary cannot be vendored. It is
    not a synonym for "CPT": a real CPT code mislabelled ICD-10 lands in
    "not_found" instead.
    """
    vocabulary_status: Literal["verified", "not_found", "unchecked"]


class CodingOutput(BaseModel):
    agent_name: Literal["coding"] = "coding"
    codes: list[CodeSuggestion]
    confidence: float = Field(ge=0.0, le=1.0)
    # Names both vendored CMS releases, so a stored result is traceable to
    # the exact pair of vocabularies that produced it.
    vocabulary_version: str

    @computed_field
    @property
    def verified_count(self) -> int:
        return sum(1 for c in self.codes
                   if c.vocabulary_status == "verified")

    @computed_field
    @property
    def not_found_count(self) -> int:
        return sum(1 for c in self.codes
                   if c.vocabulary_status == "not_found")


# ---------- Orchestrator response ----------

class PipelineResult(BaseModel):
    encounter_id: int
    note_id: int
    prior_auth: PriorAuthOutput | None = None
    care_gap: CareGapOutput | None = None
    coding: CodingOutput | None = None
    errors: dict[str, str] = Field(default_factory=dict)
