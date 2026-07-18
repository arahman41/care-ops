"""P2-1: prior-auth agent produces a valid PriorAuthOutput, and malformed
model output raises a clear parse error. The LLM call and registry logging
are faked here; a real call is exercised separately by hand (see the spec's
live-verification step).
"""
from __future__ import annotations

import pytest

from services.agent_prior_auth.agent import PriorAuthError, run
from shared.schemas import AgentInput, SoapNote

SOAP = SoapNote(subjective="Knee pain after a fall.",
                objective="Swelling and tenderness noted.",
                assessment="Suspected meniscus tear.",
                plan="Order MRI, refer to orthopedics.")

INPUT = AgentInput(encounter_id=1, note_id=1, soap=SOAP)


def _fake_call(monkeypatch, response: str):
    monkeypatch.setattr(
        "services.agent_prior_auth.agent.call",
        lambda component, system, user, max_tokens=1500,
               temperature=None: response,
    )


def _fake_log_decision(monkeypatch):
    """Returns the list of kwargs each log_decision call received."""
    calls: list[dict] = []

    def fake(**kwargs):
        calls.append(kwargs)
        return 1

    monkeypatch.setattr("services.agent_prior_auth.agent.log_decision", fake)
    return calls


def test_valid_json_produces_prior_auth_output(monkeypatch):
    _fake_call(monkeypatch, '{"items": [{"item": "MRI knee", '
                            '"reason": "advanced imaging", '
                            '"justification": "suspected meniscus tear"}], '
                            '"confidence": 0.8}')
    log_calls = _fake_log_decision(monkeypatch)

    out = run(INPUT)

    assert len(out.items) == 1
    assert out.items[0].item == "MRI knee"
    assert out.confidence == 0.8
    assert len(log_calls) == 1
    assert log_calls[0]["agent_name"] == "prior_auth"
    assert log_calls[0]["encounter_id"] == 1
    assert log_calls[0]["note_id"] == 1
    assert log_calls[0]["confidence"] == 0.8


def test_no_prior_auth_items_returns_empty_list_not_free_text(monkeypatch):
    _fake_call(monkeypatch, '{"items": [], "confidence": 0.95}')
    _fake_log_decision(monkeypatch)

    out = run(INPUT)

    assert out.items == []
    assert out.confidence == 0.95


def test_invalid_json_raises_clear_parse_error(monkeypatch):
    _fake_call(monkeypatch, "this is not json at all")
    _fake_log_decision(monkeypatch)

    with pytest.raises(PriorAuthError, match="not valid JSON"):
        run(INPUT)


def test_json_array_instead_of_object_raises_clear_parse_error(monkeypatch):
    _fake_call(monkeypatch, '[{"item": "x"}]')
    _fake_log_decision(monkeypatch)

    with pytest.raises(PriorAuthError, match="not an object"):
        run(INPUT)


def test_confidence_out_of_range_raises_clear_parse_error(monkeypatch):
    _fake_call(monkeypatch, '{"items": [], "confidence": 1.5}')
    _fake_log_decision(monkeypatch)

    with pytest.raises(PriorAuthError, match="PriorAuthOutput schema"):
        run(INPUT)


def test_parse_error_preview_is_truncated(monkeypatch):
    long_garbage = "x" * 500
    _fake_call(monkeypatch, long_garbage)
    _fake_log_decision(monkeypatch)

    with pytest.raises(PriorAuthError) as exc_info:
        run(INPUT)
    assert "..." in str(exc_info.value)
    assert len(str(exc_info.value)) < 500
