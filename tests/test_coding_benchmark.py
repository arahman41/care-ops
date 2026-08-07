"""P2-4 benchmark orchestration. Execution is faked; nothing here spends."""
from __future__ import annotations

from governance.coding_benchmark import build_soap_from_reference, plan_is_empty

FUSED_ONLY = (
    "CHIEF COMPLAINT\r\n\r\nCough.\r\n\r\n"
    "ASSESSMENT AND PLAN\r\n\r\nURI. Rest.\r\n"
)
SEPARATE_PLAN = (
    "CHIEF COMPLAINT\r\n\r\nCough.\r\n\r\n"
    "ASSESSMENT AND PLAN\r\n\r\nURI.\r\n\r\n"
    "PLAN\r\n\r\nRest and fluids.\r\n"
)


def test_soap_concatenates_primary_bucket_bodies():
    soap = build_soap_from_reference(FUSED_ONLY)
    assert soap.subjective == "Cough."
    assert soap.assessment == "URI. Rest."     # fused section -> assessment
    assert soap.plan == ""                       # nothing maps to plan


def test_fused_only_note_has_empty_plan():
    assert plan_is_empty(build_soap_from_reference(FUSED_ONLY)) is True


def test_fused_note_with_a_separate_plan_header_is_not_empty_plan():
    # This is the 24-note case the stratification exists to separate from the 27.
    soap = build_soap_from_reference(SEPARATE_PLAN)
    assert soap.plan == "Rest and fluids."
    assert plan_is_empty(soap) is False


def test_both_arms_receive_byte_identical_input():
    a = build_soap_from_reference(FUSED_ONLY)
    b = build_soap_from_reference(FUSED_ONLY)
    assert a.model_dump_json() == b.model_dump_json()
