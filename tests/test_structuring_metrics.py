"""The structuring metric math (P1-4).

This is the headline number. The arithmetic is pinned by a hand-computed
vector so the test asserts what the metric *should* be, not merely what the
implementation happens to produce. Every guard here exists because the
corresponding bug would return a plausible number rather than an error.

The metric's deliberate asymmetry, restated because it is the thing a
reviewer will challenge first:

  recall    is scored against the clinician note (the gold for what matters)
  precision is scored against the transcript    (the gold for what is true)

A generated fact that is in the transcript but absent from the clinician note
is a legitimate inclusion, because the note is a selective summary. A
generated fact supported by neither is a hallucination.
"""
from __future__ import annotations

import pytest

from governance.evaluate import StructuringCounts, score_structuring


def test_hand_computed_vector():
    # Worked by hand, independent of the implementation:
    #   recall    = correctly_placed / ref_facts = 6/10  = 0.60
    #   precision = supported / gen_facts        = 9/12  = 0.75
    #   f1        = 2(.75)(.60) / (.75 + .60)    = 0.90/1.35
    #   accuracy  = correctly_placed / captured  = 6/8   = 0.75
    counts = StructuringCounts(ref_facts=10, captured=8, correctly_placed=6,
                               gen_facts=12, supported=9)
    m = score_structuring(counts)

    assert m["recall"] == pytest.approx(0.60)
    assert m["precision"] == pytest.approx(0.75)
    assert m["f1"] == pytest.approx(0.9 / 1.35)
    assert m["accuracy"] == pytest.approx(0.75)
    assert m["hallucination_rate"] == pytest.approx(0.25)


def test_a_perfect_run_scores_one_everywhere():
    m = score_structuring(StructuringCounts(
        ref_facts=10, captured=10, correctly_placed=10,
        gen_facts=10, supported=10))
    assert m["recall"] == 1.0
    assert m["precision"] == 1.0
    assert m["f1"] == 1.0
    assert m["accuracy"] == 1.0
    assert m["hallucination_rate"] == 0.0


def test_capturing_everything_but_misfiling_it_all_scores_zero_recall():
    # The case the metric exists to catch: the model found every fact and put
    # every one in the wrong SOAP section. Capture is perfect, structuring is
    # worthless, and the headline must say so.
    m = score_structuring(StructuringCounts(
        ref_facts=10, captured=10, correctly_placed=0,
        gen_facts=10, supported=10))
    assert m["recall"] == 0.0
    assert m["accuracy"] == 0.0        # placement accuracy, not capture
    assert m["f1"] == 0.0
    assert m["precision"] == 1.0       # nothing was invented, just misfiled


def test_a_fully_hallucinated_note_scores_zero_precision():
    m = score_structuring(StructuringCounts(
        ref_facts=10, captured=0, correctly_placed=0,
        gen_facts=8, supported=0))
    assert m["precision"] == 0.0
    assert m["hallucination_rate"] == 1.0
    assert m["f1"] == 0.0


@pytest.mark.parametrize("counts", [
    StructuringCounts(0, 0, 0, 0, 0),            # nothing at all
    StructuringCounts(10, 0, 0, 0, 0),           # model returned nothing
    StructuringCounts(0, 0, 0, 10, 5),           # reference had nothing
])
def test_empty_inputs_do_not_divide_by_zero(counts):
    m = score_structuring(counts)
    assert all(0.0 <= v <= 1.0 for v in m.values())


def test_every_metric_stays_in_the_unit_interval():
    m = score_structuring(StructuringCounts(
        ref_facts=7, captured=5, correctly_placed=3, gen_facts=9, supported=4))
    for name, value in m.items():
        assert 0.0 <= value <= 1.0, f"{name} out of range: {value}"


def test_counts_that_are_impossible_are_rejected():
    # correctly_placed can never exceed captured, and captured can never
    # exceed the number of reference facts. If the harness ever produces
    # these, a counting bug has silently inflated the score.
    with pytest.raises(ValueError):
        score_structuring(StructuringCounts(
            ref_facts=5, captured=6, correctly_placed=0, gen_facts=1, supported=0))
    with pytest.raises(ValueError):
        score_structuring(StructuringCounts(
            ref_facts=5, captured=3, correctly_placed=4, gen_facts=1, supported=0))
    with pytest.raises(ValueError):
        score_structuring(StructuringCounts(
            ref_facts=5, captured=3, correctly_placed=2, gen_facts=1, supported=2))
