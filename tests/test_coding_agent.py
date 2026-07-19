"""Mock target matters. agent.py does `from shared.llm import call`, which
binds the name into services.agent_coding.agent at import time. Patching
shared.llm.call has NO effect on the already-bound reference, so every
monkeypatch below targets services.agent_coding.agent.* specifically. This
is the same trap documented in the P2-1 spec.
"""
import json

import pytest

from services.agent_coding import agent as coding_agent
from shared import vocab
from shared.llm import TruncatedResponseError
from shared.schemas import AgentInput, SoapNote

SOAP = SoapNote(subjective="s", objective="o", assessment="a", plan="p")
INPUT = AgentInput(encounter_id=1, note_id=1, soap=SOAP)


def _patch(monkeypatch, response):
    """Returns a dict that captures the log_decision kwargs."""
    def fake_call(component, system, user, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(coding_agent, "call", fake_call)
    logged = {}
    monkeypatch.setattr(coding_agent, "log_decision",
                        lambda **kw: logged.update(kw))
    return logged


def _one(system, code, **kw):
    entry = {"system": system, "code": code, "description": "d", **kw}
    return json.dumps({"codes": [entry], "confidence": 0.8})


# ---------- parsing guards ----------

def test_malformed_json_raises_coding_error(monkeypatch):
    _patch(monkeypatch, "not json at all")
    with pytest.raises(coding_agent.CodingError):
        coding_agent.run(INPUT)


def test_json_array_instead_of_object_raises_coding_error(monkeypatch):
    """A bare array raises TypeError from **data, which neither the
    MalformedJSONError nor the ValidationError handler catches. This is the
    guard that is easy to leave out."""
    _patch(monkeypatch, '[{"system": "ICD-10"}]')
    with pytest.raises(coding_agent.CodingError):
        coding_agent.run(INPUT)


def test_confidence_out_of_range_raises_coding_error(monkeypatch):
    _patch(monkeypatch, '{"codes": [], "confidence": 1.5}')
    with pytest.raises(coding_agent.CodingError):
        coding_agent.run(INPUT)


def test_error_preview_is_truncated(monkeypatch):
    _patch(monkeypatch, "x" * 5000)
    with pytest.raises(coding_agent.CodingError) as exc:
        coding_agent.run(INPUT)
    assert len(str(exc.value)) < 500


def test_truncated_response_becomes_coding_error(monkeypatch):
    """TruncatedResponseError is a RuntimeError, so without an explicit
    catch it bypasses CodingError and app.py returns 500 instead of 502.
    This agent emits the largest output of the three, so it is the most
    likely to hit the cap."""
    _patch(monkeypatch, TruncatedResponseError("coding", 1500))
    with pytest.raises(coding_agent.CodingError):
        coding_agent.run(INPUT)


# ---------- happy path and registry logging ----------

def test_empty_codes_list_round_trips(monkeypatch):
    _patch(monkeypatch, '{"codes": [], "confidence": 0.5}')
    out = coding_agent.run(INPUT)
    assert out.codes == []


def test_happy_path_logs_the_decision(monkeypatch):
    """Every agent logging to the registry is a CLAUDE.md convention, and
    P2-7 depends on it. tests/test_prior_auth_agent.py asserts the same
    fields."""
    logged = _patch(monkeypatch, _one("ICD-10", "E11.9"))
    out = coding_agent.run(INPUT)
    assert logged["encounter_id"] == 1
    assert logged["note_id"] == 1
    assert logged["agent_name"] == "coding"
    assert logged["confidence"] == out.confidence
    assert logged["output"] == out.model_dump()
    assert logged["model"] and logged["effort"]
    assert isinstance(logged["latency_ms"], int)


# ---------- enrichment: the agent computes status, the model cannot ----------

def test_real_icd10_code_is_verified(monkeypatch):
    _patch(monkeypatch, _one("ICD-10", "E11.9"))
    assert coding_agent.run(INPUT).codes[0].vocabulary_status == "verified"


def test_fabricated_code_is_not_found_and_still_returned(monkeypatch):
    _patch(monkeypatch, _one("ICD-10", "M9999"))
    out = coding_agent.run(INPUT)
    assert out.codes[0].vocabulary_status == "not_found"
    assert out.codes[0].code == "M9999"   # kept, not dropped
    assert out.not_found_count == 1


def test_cpt_code_is_unchecked_never_not_found(monkeypatch):
    _patch(monkeypatch, _one("CPT", "99213"))
    out = coding_agent.run(INPUT)
    assert out.codes[0].vocabulary_status == "unchecked"
    assert out.not_found_count == 0
    assert out.verified_count == 0


def test_hcpcs_suggestion_flows_end_to_end(monkeypatch):
    """Regression test for the 502-and-biased-sample failure that admitting
    "HCPCS" exists to prevent. Nothing else at agent level touches the
    third system value."""
    _patch(monkeypatch, _one("HCPCS", "J1885"))
    out = coding_agent.run(INPUT)
    assert out.codes[0].vocabulary_status == "verified"


def test_fabricated_code_mislabelled_cpt_is_still_not_found(monkeypatch):
    """The escape hatch, tested through the agent rather than only through
    classify. This pins that _enrich passes system through unbranched
    instead of deciding for itself."""
    _patch(monkeypatch, _one("CPT", "M9999"))
    assert coding_agent.run(INPUT).codes[0].vocabulary_status == "not_found"


def test_real_icd10_code_mislabelled_cpt_is_still_verified(monkeypatch):
    _patch(monkeypatch, _one("CPT", "E11.9"))
    assert coding_agent.run(INPUT).codes[0].vocabulary_status == "verified"


def test_real_cpt_code_mislabelled_icd10_is_not_found(monkeypatch):
    """The conservative distortion the spec admits in section 1a."""
    _patch(monkeypatch, _one("ICD-10", "99213"))
    assert coding_agent.run(INPUT).codes[0].vocabulary_status == "not_found"


def test_model_claim_of_verified_is_overridden(monkeypatch):
    """THE most important test in this file. Without it the trust boundary
    is unenforced."""
    _patch(monkeypatch, _one("ICD-10", "M9999",
                             vocabulary_status="verified"))
    assert coding_agent.run(INPUT).codes[0].vocabulary_status == "not_found"


def test_mixed_response_counts_exclude_unchecked(monkeypatch):
    _patch(monkeypatch, json.dumps({"codes": [
        {"system": "ICD-10", "code": "E11.9", "description": "d"},
        {"system": "ICD-10", "code": "M9999", "description": "d"},
        {"system": "CPT", "code": "99213", "description": "d"},
    ], "confidence": 0.8}))
    out = coding_agent.run(INPUT)
    assert out.verified_count == 1
    assert out.not_found_count == 1
    assert len(out.codes) == 3


def test_vocabulary_version_comes_from_the_module(monkeypatch):
    _patch(monkeypatch, _one("ICD-10", "E11.9"))
    assert coding_agent.run(INPUT).vocabulary_version == vocab.VOCAB_VERSION


def test_model_cannot_set_vocabulary_version(monkeypatch):
    _patch(monkeypatch, json.dumps({
        "codes": [], "confidence": 0.5, "vocabulary_version": "attacker"}))
    assert coding_agent.run(INPUT).vocabulary_version == vocab.VOCAB_VERSION
