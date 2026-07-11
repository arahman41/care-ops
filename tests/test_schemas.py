"""Contract tests: agent outputs must be structured with valid confidence."""
import pytest
from pydantic import ValidationError

from shared.schemas import (SoapNote, PriorAuthOutput, CareGapOutput,
                            CodeSuggestion)


def test_soap_note_requires_all_sections():
    with pytest.raises(ValidationError):
        SoapNote(subjective="s", objective="o", assessment="a")  # missing plan


def test_confidence_must_be_in_range():
    with pytest.raises(ValidationError):
        PriorAuthOutput(items=[], confidence=1.5)


def test_care_gap_defaults_agent_name():
    out = CareGapOutput(gaps=[], confidence=1.0)
    assert out.agent_name == "care_gap"


def test_code_suggestion_system_is_constrained():
    with pytest.raises(ValidationError):
        CodeSuggestion(system="LOINC", code="x", description="y")
