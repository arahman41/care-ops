"""P2-4 dedup, denominators, and the floor. Pure; no API, no DB.

The conflict rule and the dedup key are the highest-leverage choices in the
metric (spec §1). These tests pin them hard.
"""
from __future__ import annotations

import pytest

from governance.coding_metrics import (
    aggregate_arm, dedupe_note, floor_band, note_agreement, note_denominators,
    _CODE_SHAPE_RE, _PLACEHOLDERS,
)
from shared.schemas import CodeSuggestion, CodingOutput
from shared import vocab


def _out(*triples) -> CodingOutput:
    """Build a CodingOutput from (system, code) or (system, code, status) triples.
    Status defaults to what classify would assign, matching the real agent path.
    """
    codes = []
    for t in triples:
        system, code = t[0], t[1]
        status = t[2] if len(t) > 2 else vocab.classify(system, code)
        codes.append(CodeSuggestion(system=system, code=code, description="d",
                                    vocabulary_status=status))
    return CodingOutput(codes=codes, confidence=0.9,
                        vocabulary_version=vocab.VOCAB_VERSION)


def test_a_doubled_code_counts_once():
    # E11.9 twice within one note is one deduped observation.
    note = _out(("ICD-10", "E11.9"), ("ICD-10", "E11.9"))
    deduped = dedupe_note(note)
    assert len(deduped) == 1
    assert deduped[0].status == "verified"


def test_dedup_key_is_normalize_alone_not_system_plus_code():
    # Same code, two systems -> ONE deduped observation, not two.
    note = _out(("ICD-10", "E11.9"), ("CPT", "E11.9"))
    deduped = dedupe_note(note)
    assert len(deduped) == 1
    assert set(deduped[0].systems_seen) == {"ICD-10", "CPT"}


def test_not_found_beats_unchecked_on_conflict():
    # A code absent from both vocabularies, emitted once as CPT (unchecked) and
    # once as ICD-10 (not_found), resolves to not_found (spec §1).
    note = _out(("CPT", "88888"), ("ICD-10", "88888"))
    deduped = dedupe_note(note)
    assert len(deduped) == 1
    assert deduped[0].status == "not_found"


def test_verified_never_conflicts():
    # E11.9 verifies under any label; a second CPT-labelled occurrence cannot
    # demote it, because classify rules 1-2 ignore the label.
    note = _out(("CPT", "E11.9"), ("ICD-10", "E11.9"))
    assert dedupe_note(note)[0].status == "verified"


def test_denominators_exclude_unchecked_from_checkable():
    note = _out(("ICD-10", "E11.9"),      # verified
                ("ICD-10", "M9999"),      # not_found
                ("CPT", "99213"))         # unchecked
    d = note_denominators(dedupe_note(note))
    assert d.verified == 1 and d.not_found == 1 and d.unchecked == 1
    assert d.checkable == 2 and d.total == 3


def test_placeholders_are_cause_4_not_cause_1():
    for token in ("NONE", "UNKNOWN", "TBD"):
        assert token in _PLACEHOLDERS
        note = _out(("ICD-10", token))          # not_found, degenerate
        band = floor_band(dedupe_note(note))
        assert band.cause4 == 1 and band.cause1 == 0


def test_slash_form_na_is_cause_4_by_shape():
    # normalize strips only the dot, so "N/A" keeps its slash and fails shape.
    assert not _CODE_SHAPE_RE.match(vocab.normalize("N/A"))
    band = floor_band(dedupe_note(_out(("ICD-10", "N/A"))))
    assert band.cause4 == 1


def test_cpt_shaped_not_found_is_cause_3_upper_bound():
    # 99213 declared ICD-10 is not_found (real CPT mislabelled), CPT-shaped ->
    # cause 3. It is an UPPER bound: a fabricated 5-digit ICD-10 code is
    # indistinguishable and lands here too.
    band = floor_band(dedupe_note(_out(("ICD-10", "99213"))))
    assert band.cause3 == 1 and band.cause1 == 0


def test_cpt_shape_predicate_is_inclusive_over_retained_labels():
    # A code carrying several labels satisfies cause 3 if ANY retained label is
    # not CPT (spec §5b). 99213 as {CPT, ICD-10}, absent from vocab, resolves
    # not_found (ICD-10 occurrence) and is CPT-shaped with a non-CPT label.
    note = _out(("CPT", "99213"), ("ICD-10", "99213"))
    deduped = dedupe_note(note)
    assert deduped[0].status == "not_found"
    assert floor_band(deduped).cause3 == 1


def test_fabricated_icd_shaped_code_is_cause_1_residual():
    # M9999: ICD-shaped, not CPT-shaped, not degenerate, absent -> fabricated.
    band = floor_band(dedupe_note(_out(("ICD-10", "M9999"))))
    assert band.cause1 == 1
    assert band.cause3 == 0 and band.cause4 == 0


def test_floor_band_bounds_are_percentage_points():
    # 1 verified + 1 not_found(fabricated) => checkable 2, not_found_rate 50 pts.
    note = _out(("ICD-10", "E11.9"), ("ICD-10", "M9999"))
    band = floor_band(dedupe_note(note))
    assert band.upper == pytest.approx(50.0)      # points, not 0.5
    assert band.lower == pytest.approx(0.0)       # no cause 3/4 here


# ---------- pooled per-arm aggregation and agreement (Task 2.2) ----------


def test_aggregate_is_pooled_ratio_of_sums_in_points():
    # Note A: 1 verified, 1 not_found. Note B: 2 verified, 0 not_found.
    # Pooled verified rate = 3/4 = 75 pts; not_found rate = 25 pts.
    notes = [
        dedupe_note(_out(("ICD-10", "E11.9"), ("ICD-10", "M9999"))),
        dedupe_note(_out(("ICD-10", "E11.9"), ("ICD-10", "I10"))),
    ]
    s = aggregate_arm(notes)
    assert s.verified_rate == pytest.approx(75.0)
    assert s.not_found_rate == pytest.approx(25.0)
    assert s.n_notes == 2 and s.checkable == 4


def test_verified_rate_is_none_on_empty_checkable():
    # All unchecked -> checkable 0 -> rate None, never 0.0 (spec §1, verified_rate).
    notes = [dedupe_note(_out(("CPT", "99213")))]
    s = aggregate_arm(notes)
    assert s.verified_rate is None and s.not_found_rate is None


def test_pessimistic_counts_unchecked_as_not_verified():
    # 1 verified, 1 unchecked -> standard verified rate 100 pts (unchecked
    # excluded); pessimistic 50 pts (unchecked counted against).
    notes = [dedupe_note(_out(("ICD-10", "E11.9"), ("CPT", "99213")))]
    s = aggregate_arm(notes)
    assert s.verified_rate == pytest.approx(100.0)
    assert s.pessimistic_verified_rate == pytest.approx(50.0)
    assert s.unchecked_share == pytest.approx(50.0)


def test_agreement_is_jaccard_over_normalized_keys():
    a = dedupe_note(_out(("ICD-10", "E11.9"), ("ICD-10", "I10")))
    b = dedupe_note(_out(("ICD-10", "E11.9"), ("ICD-10", "M9999")))
    assert note_agreement(a, b) == pytest.approx(1 / 3)   # {E119} / {E119,I10,M9999}


def test_agreement_is_none_when_neither_arm_emitted_a_code():
    assert note_agreement([], []) is None
