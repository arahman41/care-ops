"""The rules engine is deterministic, so it is fully unit testable."""
from services.agent_care_gap.rules import find_gaps


def test_diabetes_triggers_a1c_rule():
    hits = find_gaps("Patient has diabetes and elevated blood sugar.")
    assert any(h["rule_id"] == "A1C_OVERDUE" for h in hits)


def test_clean_note_has_no_gaps():
    assert find_gaps("Patient here for a routine ankle check.") == []
