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


# ---------- per-arm cached execution (Task 6.4) ----------

import pytest  # noqa: E402

import governance.coding_benchmark as cb  # noqa: E402
from governance.llm_cache import Cache  # noqa: E402
from shared.llm import LLMResult  # noqa: E402
from shared.schemas import SoapNote  # noqa: E402

SOAP = SoapNote(subjective="s", objective="o", assessment="a", plan="p")


def _fake_detailed(monkeypatch, result_or_exc, calls=None):
    def fake(component, system, user, max_tokens, model=None, effort=None):
        if calls is not None:
            calls.append((model, effort))
        if isinstance(result_or_exc, Exception):
            raise result_or_exc
        return result_or_exc
    monkeypatch.setattr(cb, "call_detailed", fake)


def _ok(model="claude-sonnet-5-20260101", text='{"codes": [], "confidence": 0.5}'):
    return LLMResult(text=text, model=model, input_tokens=10,
                     output_tokens=20, stop_reason="end_turn")


def test_run_arm_passes_the_override_and_returns_output(monkeypatch, tmp_path):
    calls = []
    _fake_detailed(monkeypatch, _ok(), calls)
    r = cb.run_arm_on_note(SOAP, model="claude-sonnet-5", effort="xhigh",
                           cache=Cache(tmp_path))
    assert calls == [("claude-sonnet-5", "xhigh")]     # override reached the call
    assert r.failure is None and r.output is not None
    assert r.observed_model == "claude-sonnet-5-20260101"
    assert r.latency_ms is not None                     # cold call has latency


def test_cache_hit_reconstructs_llmresult_and_drops_latency(monkeypatch, tmp_path):
    cache = Cache(tmp_path)
    _fake_detailed(monkeypatch, _ok())
    cb.run_arm_on_note(SOAP, model="claude-sonnet-5", effort="xhigh", cache=cache)
    # Second call must not hit the API: make the fake explode if it runs.
    _fake_detailed(monkeypatch, RuntimeError("must not call API on a hit"))
    r = cb.run_arm_on_note(SOAP, model="claude-sonnet-5", effort="xhigh", cache=cache)
    assert r.output is not None
    assert r.observed_model == "claude-sonnet-5-20260101"  # survived the hit
    assert r.latency_ms is None                            # latency does not


def test_truncation_is_a_typed_failure_not_an_abort(monkeypatch, tmp_path):
    from shared.llm import TruncatedResponseError
    _fake_detailed(monkeypatch, TruncatedResponseError("coding", 5000))
    r = cb.run_arm_on_note(SOAP, model="claude-opus-4-8", effort="high",
                           cache=Cache(tmp_path))
    assert r.output is None and "truncat" in r.failure.lower()


def test_parse_failure_is_typed_and_keeps_the_llmresult(monkeypatch, tmp_path):
    _fake_detailed(monkeypatch, _ok(text="not json"))
    r = cb.run_arm_on_note(SOAP, model="claude-sonnet-5", effort="xhigh",
                           cache=Cache(tmp_path))
    assert r.output is None and r.failure is not None
    assert r.tokens == (10, 20)         # tokens still captured for cost accounting


def test_observed_model_family_mismatch_fails_the_run(monkeypatch, tmp_path):
    _fake_detailed(monkeypatch, _ok(model="claude-opus-4-8-20260101"))
    with pytest.raises(ValueError, match="observed model"):
        cb.run_arm_on_note(SOAP, model="claude-sonnet-5", effort="xhigh",
                           cache=Cache(tmp_path))


def test_cache_key_and_artifact_prompt_version_cannot_drift():
    # The plan's executor notes: _cache_key must be built FROM
    # _cache_version_string, so the key the response was stored under and the
    # prompt_version recorded in the artifact are the same string by
    # construction, not by two copies of one f-string agreeing.
    from governance.llm_cache import cache_key
    version = cb._cache_version_string("xhigh")
    assert cb._cache_key("claude-sonnet-5", "xhigh", "payload") == cache_key(
        "coding", "claude-sonnet-5", version, "payload")


def test_cache_version_string_folds_in_effort_and_max_tokens():
    # Effort, prompt hash, and max_tokens all belong in the key: the bare
    # PROMPT_VERSION = "v1" literal used elsewhere in governance/ is the exact
    # failure that blends two prompt versions into one number.
    v_x = cb._cache_version_string("xhigh")
    v_h = cb._cache_version_string("high")
    assert v_x != v_h                       # effort changes the key
    assert "max5000" in v_x                 # max_tokens is folded in
    assert len(v_x.split("|")) == 3         # effort|prompt_hash|max_tokens
