"""P2-4 routing seam: call_detailed() exposes what call() discards, and
carries per-arm (model, effort) overrides all the way to the outbound request.

Mock target: shared.llm._client.messages.create. call_detailed builds kwargs
and calls the client, so patching the client captures the real outbound request.
This is the test that a naive 'override in run()' implementation fails.
"""
from __future__ import annotations

import pytest

import shared.llm as llm
from shared.llm import LLMResult, _UNSET, call, call_detailed


class _Resp:
    """Minimal stand-in for an anthropic Message."""
    def __init__(self, text="ok", model="claude-sonnet-5-20260101",
                 stop_reason="end_turn", input_tokens=11, output_tokens=22):
        self.stop_reason = stop_reason
        self.model = model
        self.content = [type("Block", (), {"type": "text", "text": text})()]
        self.usage = type("Usage", (), {"input_tokens": input_tokens,
                                        "output_tokens": output_tokens})()


def _capture(monkeypatch, resp=None):
    """Patch the client; return a dict that captures the outbound kwargs."""
    captured = {}
    def fake_create(**kwargs):
        captured.update(kwargs)
        return resp or _Resp()
    monkeypatch.setattr(llm._client.messages, "create", fake_create)
    return captured


def test_llmresult_is_frozen():
    r = LLMResult(text="t", model="m", input_tokens=1, output_tokens=2,
                  stop_reason="end_turn")
    with pytest.raises(Exception):
        r.text = "x"          # frozen dataclass


def test_call_detailed_returns_observed_model_and_tokens(monkeypatch):
    _capture(monkeypatch, _Resp(model="claude-opus-4-8-20260101",
                                input_tokens=7, output_tokens=9))
    r = call_detailed("coding", system="s", user="u", max_tokens=100)
    assert r.model == "claude-opus-4-8-20260101"   # resp.model, not requested
    assert r.input_tokens == 7 and r.output_tokens == 9
    assert r.text == "ok"


def test_call_detailed_model_override_reaches_the_outbound_request(monkeypatch):
    captured = _capture(monkeypatch)
    call_detailed("coding", system="s", user="u", max_tokens=100,
                  model="claude-opus-4-8")
    assert captured["model"] == "claude-opus-4-8"    # NOT ROUTING's default


def test_call_detailed_effort_override_reaches_the_outbound_request(monkeypatch):
    captured = _capture(monkeypatch)
    call_detailed("coding", system="s", user="u", max_tokens=100,
                  effort="high")
    assert captured["output_config"] == {"effort": "high"}


def test_default_model_and_effort_come_from_routing(monkeypatch):
    captured = _capture(monkeypatch)
    call_detailed("coding", system="s", user="u", max_tokens=100)
    # ROUTING["coding"] default is (claude-opus-4-8, high), set by the P2-4
    # benchmark (artifact coding_20260807T214249Z.json). It was
    # (claude-sonnet-5, xhigh) before that run. If this assertion fails,
    # production coding routing moved: confirm it was a deliberate,
    # evidence-backed change and not an accidental edit.
    assert captured["model"] == "claude-opus-4-8"
    assert captured["output_config"] == {"effort": "high"}


def test_unset_effort_is_distinct_from_explicit_none(monkeypatch):
    # The sentinel distinction, tested on "coding" whose ROUTING effort is xhigh:
    #   effort=_UNSET (or omitted) -> fall back to ROUTING -> output_config high
    #   effort=None                -> explicit "no effort" -> NO output_config
    # A plain effort=None default would collide these two, sending no effort even
    # when the caller wanted the routed default.
    unset = _capture(monkeypatch)
    call_detailed("coding", system="s", user="u", max_tokens=100, effort=_UNSET)
    assert unset["output_config"] == {"effort": "high"}

    explicit = _capture(monkeypatch)
    call_detailed("coding", system="s", user="u", max_tokens=100,
                  effort=None, temperature=0)
    assert "output_config" not in explicit           # explicit None overrides
    assert explicit["temperature"] == 0


def test_call_detailed_raises_on_truncation_before_building_result(monkeypatch):
    _capture(monkeypatch, _Resp(stop_reason="max_tokens"))
    with pytest.raises(llm.TruncatedResponseError):
        call_detailed("coding", system="s", user="u", max_tokens=10)


def test_call_is_a_thin_wrapper_returning_text(monkeypatch):
    _capture(monkeypatch, _Resp(text="hello"))
    assert call("coding", system="s", user="u", max_tokens=100) == "hello"


def test_call_signature_is_unchanged_for_existing_callers(monkeypatch):
    # services/intake/structure.py calls call(component, system=, user=,
    # max_tokens=). tests/test_structure.py fakes exactly that shape. If call()'s
    # positional/keyword contract changes, those callers break.
    import inspect
    sig = inspect.signature(call)
    assert list(sig.parameters)[:4] == ["component", "system", "user", "max_tokens"]


# ---------- LLMResult serialization for the cache (P2-4 §8) ----------

def test_llmresult_json_roundtrip_preserves_model_and_tokens():
    r = LLMResult(text="hi", model="claude-opus-4-8-20260101",
                  input_tokens=7, output_tokens=9, stop_reason="end_turn")
    back = LLMResult.from_json(r.to_json())
    assert back == r
    assert back.model == "claude-opus-4-8-20260101"
    assert back.input_tokens == 7 and back.output_tokens == 9


def test_observed_model_and_tokens_survive_a_cache_roundtrip(tmp_path):
    from governance.llm_cache import Cache
    cache = Cache(tmp_path)
    r = LLMResult(text="t", model="claude-sonnet-5-20260101",
                  input_tokens=3, output_tokens=4, stop_reason="end_turn")
    cache.put("k", r.to_json())
    back = LLMResult.from_json(cache.get("k"))
    assert back.model == "claude-sonnet-5-20260101"
    assert (back.input_tokens, back.output_tokens) == (3, 4)
