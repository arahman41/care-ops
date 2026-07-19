"""Contract tests: agent outputs must be structured with valid confidence."""
import pytest
from pydantic import ValidationError

from shared.schemas import (SoapNote, PriorAuthOutput, CareGapOutput,
                            CareGapSource, CodeSuggestion, CodingOutput,
                            ModelCodeSuggestion, ModelCodingPayload)


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
    # vocabulary_status supplied explicitly so this still tests the bad
    # `system` rejection it was written for, rather than passing because a
    # newly required field happens to be missing too.
    with pytest.raises(ValidationError):
        CodeSuggestion(system="LOINC", code="x", description="y",
                       vocabulary_status="verified")


def test_care_gap_source_requires_core_citation_fields():
    with pytest.raises(ValidationError):
        CareGapSource(organization="USPSTF", title="x", year=2021)  # no url


def test_care_gap_source_grade_is_optional():
    src = CareGapSource(organization="USPSTF", title="x", year=2021,
                        url="https://example.org")
    assert src.grade is None


def test_model_payload_discards_a_claimed_vocabulary_status():
    """The trust boundary, tested at the schema level.

    The agent test pins observable behaviour, but it does so through
    pydantic's extra="ignore" default, which is config-sensitive. If a base
    config ever set extra="allow", the model would regain a channel to
    certify its own codes and the agent test might still pass.
    """
    suggestion = ModelCodeSuggestion(
        system="ICD-10", code="M9999", description="fabricated",
        vocabulary_status="verified",
    )
    assert not hasattr(suggestion, "vocabulary_status")


def test_model_payload_accepts_hcpcs_system():
    """Rejecting an honest HCPCS label would 502 on exactly the notes
    mentioning drugs and supplies, biasing the sample."""
    payload = ModelCodingPayload(
        codes=[ModelCodeSuggestion(
            system="HCPCS", code="J1885", description="Ketorolac injection")],
        confidence=0.8,
    )
    assert payload.codes[0].system == "HCPCS"


def test_counts_exclude_unchecked_from_both_sides():
    out = CodingOutput(
        codes=[
            CodeSuggestion(system="ICD-10", code="E11.9", description="a",
                           vocabulary_status="verified"),
            CodeSuggestion(system="ICD-10", code="M9999", description="b",
                           vocabulary_status="not_found"),
            CodeSuggestion(system="CPT", code="99213", description="c",
                           vocabulary_status="unchecked"),
        ],
        confidence=0.7, vocabulary_version="test-vocab",
    )
    assert out.verified_count == 1
    assert out.not_found_count == 1


def test_computed_counts_are_serialized_for_the_registry():
    """They must reach the agent_decisions output JSONB column."""
    out = CodingOutput(codes=[], confidence=0.5, vocabulary_version="v")
    dumped = out.model_dump()
    assert dumped["verified_count"] == 0
    assert dumped["not_found_count"] == 0
