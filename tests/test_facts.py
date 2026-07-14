"""Atomic-fact decomposition (P1-4).

The design invariant this file exists to protect: a reference fact's SOAP
label comes from the *human* section header it was drawn from, never from a
model. The decomposer is called once per section and is shown only that
section's body, so it cannot see, choose, or mis-assign a label. That is what
makes "the model put this fact in the wrong section" a meaningful claim.
"""
from __future__ import annotations

import pytest

from governance import facts as facts_mod
from governance.aci_sections import ASSESSMENT, OBJECTIVE, PLAN, SUBJECTIVE
from governance.facts import Fact, decompose_reference, decompose_soap
from governance.llm_cache import Cache
from shared.llm import MalformedJSONError
from shared.schemas import SoapNote


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path)


def _stub(monkeypatch, response):
    """Pin the model's reply. Accepts a string or a per-call list."""
    replies = [response] if isinstance(response, str) else list(response)
    calls = []

    def fake_call(component, system, user, max_tokens=1500, temperature=None):
        calls.append({"component": component, "user": user,
                      "temperature": temperature})
        return replies.pop(0) if len(replies) > 1 else replies[0]

    monkeypatch.setattr(facts_mod, "call", fake_call)
    return calls


NOTE = (
    "CHIEF COMPLAINT\n\nCough.\n\n"
    "ASSESSMENT AND PLAN\n\nUpper respiratory infection. Rest and fluids.\n"
)


def test_a_fact_inherits_its_label_from_the_human_header(monkeypatch, cache):
    _stub(monkeypatch, '["Patient reports a cough."]')
    out = decompose_reference(NOTE, cache=cache)

    cc = [f for f in out if f.source_header == "CHIEF COMPLAINT"]
    assert cc[0].acceptable == frozenset({SUBJECTIVE})


def test_a_fact_from_a_fused_section_accepts_either_assessment_or_plan(
        monkeypatch, cache):
    _stub(monkeypatch, '["Upper respiratory infection."]')
    out = decompose_reference(NOTE, cache=cache)

    fused = [f for f in out if f.source_header == "ASSESSMENT AND PLAN"]
    assert fused[0].acceptable == frozenset({ASSESSMENT, PLAN})


def test_the_decomposer_never_sees_the_section_label(monkeypatch, cache):
    # If the label leaked into the prompt, the model could infer it, and the
    # "human-derived label" guarantee would be a fiction.
    calls = _stub(monkeypatch, '["Cough."]')
    decompose_reference(NOTE, cache=cache)

    for c in calls:
        assert "CHIEF COMPLAINT" not in c["user"]
        assert "ASSESSMENT AND PLAN" not in c["user"]


def test_the_judge_model_is_pinned_to_temperature_zero(monkeypatch, cache):
    calls = _stub(monkeypatch, '["Cough."]')
    decompose_reference(NOTE, cache=cache)
    assert all(c["temperature"] == 0 for c in calls)
    assert all(c["component"] == "eval_judge" for c in calls)


def test_every_returned_line_becomes_one_fact(monkeypatch, cache):
    _stub(monkeypatch, '["Cough for three days.", "No fever.", "No chills."]')
    out = decompose_reference("CHIEF COMPLAINT\n\nCough, no fever or chills.\n",
                              cache=cache)
    assert [f.text for f in out] == [
        "Cough for three days.", "No fever.", "No chills."]


def test_a_generated_note_decomposes_per_soap_section(monkeypatch, cache):
    _stub(monkeypatch, '["A fact."]')
    soap = SoapNote(subjective="s", objective="o", assessment="a", plan="p")
    out = decompose_soap(soap, cache=cache)

    # One fact per section, each tagged with the section it came from.
    assert {f.source_header for f in out} == {
        SUBJECTIVE, OBJECTIVE, ASSESSMENT, PLAN}


def test_an_empty_section_contributes_no_facts(monkeypatch, cache):
    _stub(monkeypatch, '["A fact."]')
    soap = SoapNote(subjective="s", objective="", assessment="   ", plan="p")
    out = decompose_soap(soap, cache=cache)
    assert {f.source_header for f in out} == {SUBJECTIVE, PLAN}


def test_malformed_model_output_raises(monkeypatch, cache):
    _stub(monkeypatch, "not json at all")
    with pytest.raises(MalformedJSONError):
        decompose_reference(NOTE, cache=cache)


def test_a_json_object_instead_of_an_array_raises(monkeypatch, cache):
    _stub(monkeypatch, '{"facts": ["Cough."]}')
    with pytest.raises(ValueError, match="array"):
        decompose_reference(NOTE, cache=cache)


def test_results_are_cached_so_a_replay_costs_nothing(monkeypatch, cache):
    calls = _stub(monkeypatch, '["Cough."]')
    first = decompose_reference(NOTE, cache=cache)
    n_after_first = len(calls)

    second = decompose_reference(NOTE, cache=cache)
    assert len(calls) == n_after_first      # no new API calls
    assert [f.text for f in first] == [f.text for f in second]


def test_facts_are_hashable_and_carry_their_acceptable_set():
    f = Fact(text="Cough.", acceptable=frozenset({SUBJECTIVE}),
             source_header="CHIEF COMPLAINT")
    assert f in {f}
