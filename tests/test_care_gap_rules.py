"""P2-2: the rules engine is deterministic, so it is fully unit testable.

Each rule has a firing test whose input requires the stem to extend
("diabetes", "hypertension", "lipids", "smoker"), so a regression to the
pre-P2-2 pattern shape turns the suite red. Those patterns wrapped prefix
stems as \\b(diabet|...)\\b, whose trailing \\b prevented the stem from ever
matching the real word.

test_lipid_screening_fires_on_bare_cholesterol is the deliberate exception:
"cholesterol" is a whole word and matches either way, so it pins the rule's
rename but not the regex fix. test_lipid_screening_fires_on_plural_lipids
covers LIPID_SCREENING's fix.
"""
from services.agent_care_gap.rules import CITATIONS_VERIFIED_ON, RULES, find_gaps


def _rule_ids(text: str) -> set[str]:
    return {h["rule_id"] for h in find_gaps(text)}


# ---------- A1C_MONITORING ----------

def test_a1c_monitoring_fires_on_bare_diabetes():
    assert "A1C_MONITORING" in _rule_ids("Patient has diabetes.")


def test_a1c_monitoring_does_not_fire_without_diabetes_terms():
    assert "A1C_MONITORING" not in _rule_ids("Patient here for an ankle check.")


# ---------- HTN_SCREENING ----------

def test_htn_screening_fires_on_bare_hypertension():
    assert "HTN_SCREENING" in _rule_ids("Assessment: hypertension.")


def test_htn_screening_does_not_fire_on_a_normal_bp_reading():
    # "blood pressure" alone is deliberately not a trigger: the rule fires on
    # high/elevated BP, so a normal reading must stay quiet.
    assert "HTN_SCREENING" not in _rule_ids("Blood pressure 118/76, normal.")


# ---------- LIPID_SCREENING ----------

def test_lipid_screening_fires_on_bare_cholesterol():
    assert "LIPID_SCREENING" in _rule_ids("History of high cholesterol.")


def test_lipid_screening_fires_on_plural_lipids():
    """LIPID_SCREENING's regression pin.

    Unlike the other rules, this one's spec-named stem word ("high
    cholesterol") is a whole word, so it matches even under the buggy
    trailing-\\b pattern and cannot pin the fix. "lipids" can: \\blipid\\b
    fails on it, \\blipid\\w* succeeds.
    """
    assert "LIPID_SCREENING" in _rule_ids("Recheck lipids in 3 months.")


def test_lipid_screening_does_not_fire_without_lipid_terms():
    assert "LIPID_SCREENING" not in _rule_ids("Patient reports allergies.")


# ---------- TOBACCO_CESSATION ----------

def test_tobacco_cessation_fires_on_bare_smoker():
    assert "TOBACCO_CESSATION" in _rule_ids("Patient is a smoker.")


def test_tobacco_cessation_does_not_fire_without_tobacco_terms():
    assert "TOBACCO_CESSATION" not in _rule_ids("Patient reports allergies.")


# ---------- engine-level ----------

def test_clean_note_has_no_gaps():
    assert find_gaps("Patient here for a routine ankle check.") == []


def test_evidence_records_the_matched_span():
    hits = find_gaps("Patient has diabetes.")
    a1c = next(h for h in hits if h["rule_id"] == "A1C_MONITORING")
    assert "diabetes" in a1c["evidence"]


def test_rule_ids_are_unique():
    ids = [r.rule_id for r in RULES]
    assert len(ids) == len(set(ids))


# ---------- citations ----------

def test_every_rule_has_a_complete_citation():
    """The P2-2 exit criterion, enforced in code: no rule may be uncited.

    This is what stops a future rule being added without a guideline.
    """
    for rule in RULES:
        src = rule.source
        assert src is not None, f"{rule.rule_id} has no source"
        assert src.organization, f"{rule.rule_id} has no organization"
        assert src.title, f"{rule.rule_id} has no title"
        assert src.url.startswith("https://"), f"{rule.rule_id} url: {src.url}"
        assert 2000 <= src.year <= 2100, f"{rule.rule_id} year: {src.year}"


def test_fired_gaps_carry_their_citation():
    hits = find_gaps("Patient has diabetes.")
    a1c = next(h for h in hits if h["rule_id"] == "A1C_MONITORING")
    assert a1c["source"]["organization"] == "American Diabetes Association"
    assert a1c["source"]["grade"] == "E"


def test_care_gap_item_round_trips_with_nested_source():
    """The registry logs output.model_dump(), so the nested model must survive."""
    from shared.schemas import CareGapItem

    hits = find_gaps("Patient is a smoker.")
    item = CareGapItem(**next(h for h in hits
                              if h["rule_id"] == "TOBACCO_CESSATION"))
    dumped = item.model_dump()
    assert CareGapItem(**dumped) == item
    assert dumped["source"]["grade"] == "A"


def test_citations_verified_date_is_recorded():
    assert CITATIONS_VERIFIED_ON == "2026-07-16"
