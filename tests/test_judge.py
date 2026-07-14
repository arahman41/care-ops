"""The judge, and the counting it feeds (P1-4).

The judge is the only place a model grades this project's headline number, so
its failure modes are guarded harder than anything else in the harness. The
one that matters most: if the judge returns fewer verdicts than there were
facts and the harness quietly zips them together, reference facts vanish from
the recall denominator and the score goes UP. That is a silent, plausible,
resume-bound lie, so it raises.
"""
from __future__ import annotations

import pytest

from governance import judge as judge_mod
from governance.aci_sections import ASSESSMENT, PLAN, SUBJECTIVE
from governance.facts import Fact
from governance.judge import JudgeProtocolError, judge_presence, judge_support
from governance.llm_cache import Cache
from shared.schemas import SoapNote

SOAP = SoapNote(subjective="Cough for 3 days.", objective="Lungs clear.",
                assessment="URI.", plan="Rest and fluids.")


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path)


def _stub(monkeypatch, response):
    def fake_call(component, system, user, max_tokens=1500, temperature=None):
        return response
    monkeypatch.setattr(judge_mod, "call", fake_call)


def _fact(text, acceptable, header="H"):
    return Fact(text=text, acceptable=frozenset(acceptable), source_header=header)


# ---------- placement ----------

def test_a_fact_found_in_an_acceptable_section_is_correctly_placed(
        monkeypatch, cache):
    _stub(monkeypatch, '[{"id": 1, "found": true, "section": "subjective"}]')
    verdicts = judge_presence(
        SOAP, [_fact("Cough for 3 days.", {SUBJECTIVE})], cache=cache)

    assert verdicts[0].found is True
    assert verdicts[0].correctly_placed is True


def test_a_fact_found_in_the_wrong_section_is_captured_but_not_placed(
        monkeypatch, cache):
    # This is the case the whole metric exists to detect: the model got the
    # content but structured it wrong.
    _stub(monkeypatch, '[{"id": 1, "found": true, "section": "objective"}]')
    verdicts = judge_presence(
        SOAP, [_fact("Cough for 3 days.", {SUBJECTIVE})], cache=cache)

    assert verdicts[0].found is True
    assert verdicts[0].correctly_placed is False


@pytest.mark.parametrize("section", [ASSESSMENT, PLAN])
def test_a_fused_fact_is_placed_correctly_in_either_bucket(
        monkeypatch, cache, section):
    # 51 of 120 held-out notes fuse A and P, so both answers are correct.
    _stub(monkeypatch, f'[{{"id": 1, "found": true, "section": "{section}"}}]')
    verdicts = judge_presence(
        SOAP, [_fact("URI.", {ASSESSMENT, PLAN})], cache=cache)
    assert verdicts[0].correctly_placed is True


def test_a_fact_not_found_is_neither_captured_nor_placed(monkeypatch, cache):
    _stub(monkeypatch, '[{"id": 1, "found": false, "section": null}]')
    verdicts = judge_presence(
        SOAP, [_fact("Patient has diabetes.", {SUBJECTIVE})], cache=cache)

    assert verdicts[0].found is False
    assert verdicts[0].correctly_placed is False


# ---------- the guards against a silently inflated score ----------

def test_too_few_verdicts_raises_rather_than_shrinking_the_denominator(
        monkeypatch, cache):
    # THE bug this harness must never have. Two facts in, one verdict back.
    # Zipping them would drop a reference fact and raise recall for free.
    _stub(monkeypatch, '[{"id": 1, "found": true, "section": "subjective"}]')
    two_facts = [_fact("Cough.", {SUBJECTIVE}), _fact("No fever.", {SUBJECTIVE})]

    with pytest.raises(JudgeProtocolError, match="2 facts"):
        judge_presence(SOAP, two_facts, cache=cache)


def test_too_many_verdicts_raises(monkeypatch, cache):
    _stub(monkeypatch, '[{"id": 1, "found": true, "section": "subjective"},'
                       ' {"id": 2, "found": true, "section": "subjective"}]')
    with pytest.raises(JudgeProtocolError):
        judge_presence(SOAP, [_fact("Cough.", {SUBJECTIVE})], cache=cache)


def test_duplicate_or_missing_ids_raise(monkeypatch, cache):
    _stub(monkeypatch, '[{"id": 1, "found": true, "section": "subjective"},'
                       ' {"id": 1, "found": true, "section": "subjective"}]')
    with pytest.raises(JudgeProtocolError, match="ids"):
        judge_presence(
            SOAP,
            [_fact("Cough.", {SUBJECTIVE}), _fact("No fever.", {SUBJECTIVE})],
            cache=cache)


def test_an_unknown_section_name_raises(monkeypatch, cache):
    _stub(monkeypatch, '[{"id": 1, "found": true, "section": "history"}]')
    with pytest.raises(JudgeProtocolError, match="section"):
        judge_presence(SOAP, [_fact("Cough.", {SUBJECTIVE})], cache=cache)


def test_found_with_a_null_section_raises(monkeypatch, cache):
    # "I found it but cannot say where" is not a usable verdict for a metric
    # whose entire subject is where things were put.
    _stub(monkeypatch, '[{"id": 1, "found": true, "section": null}]')
    with pytest.raises(JudgeProtocolError, match="section"):
        judge_presence(SOAP, [_fact("Cough.", {SUBJECTIVE})], cache=cache)


def test_no_facts_means_no_call_and_no_verdicts(monkeypatch, cache):
    def explode(*a, **k):
        raise AssertionError("must not call the API for an empty fact list")
    monkeypatch.setattr(judge_mod, "call", explode)
    assert judge_presence(SOAP, [], cache=cache) == []


# ---------- transcript support (precision / hallucination) ----------

def test_a_supported_generated_fact_is_not_a_hallucination(monkeypatch, cache):
    _stub(monkeypatch, '[{"id": 1, "supported": true}]')
    out = judge_support("Doctor: any cough? Patient: yes, three days.",
                        ["Cough for 3 days."], cache=cache)
    assert out == [True]


def test_an_unsupported_generated_fact_is_a_hallucination(monkeypatch, cache):
    _stub(monkeypatch, '[{"id": 1, "supported": false}]')
    out = judge_support("Doctor: any cough? Patient: yes.",
                        ["Patient has type 2 diabetes."], cache=cache)
    assert out == [False]


def test_support_verdict_count_must_match(monkeypatch, cache):
    _stub(monkeypatch, '[{"id": 1, "supported": true}]')
    with pytest.raises(JudgeProtocolError):
        judge_support("t", ["fact one", "fact two"], cache=cache)


def test_support_results_are_cached(monkeypatch, cache):
    calls = []

    def counting_call(component, system, user, max_tokens=1500, temperature=None):
        calls.append(1)
        return '[{"id": 1, "supported": true}]'

    monkeypatch.setattr(judge_mod, "call", counting_call)
    judge_support("transcript", ["a fact"], cache=cache)
    judge_support("transcript", ["a fact"], cache=cache)
    assert len(calls) == 1
