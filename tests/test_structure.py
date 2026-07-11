"""P1-2: SOAP structuring produces a valid SoapNote, and malformed model
output raises a clear parse error. The LLM call is faked here; the real
call is exercised separately (see test_structure_live.py).
"""
from __future__ import annotations

import pytest

from services.intake.structure import NoteStructuringError, structure_note


def _fake_call(monkeypatch, response: str):
    monkeypatch.setattr(
        "services.intake.structure.call",
        lambda component, system, user, max_tokens=1200: response,
    )


def test_valid_json_produces_soap_note(monkeypatch):
    _fake_call(monkeypatch, '{"subjective": "s", "objective": "o", '
                            '"assessment": "a", "plan": "p"}')
    note, model, effort = structure_note("transcript text")
    assert note.subjective == "s"
    assert note.objective == "o"
    assert note.assessment == "a"
    assert note.plan == "p"
    assert model
    assert effort


def test_code_fenced_json_is_accepted(monkeypatch):
    fenced = ('```json\n{"subjective": "s", "objective": "o", '
              '"assessment": "a", "plan": "p"}\n```')
    _fake_call(monkeypatch, fenced)
    note, _, _ = structure_note("transcript text")
    assert note.subjective == "s"


def test_plain_fence_without_json_tag_is_accepted(monkeypatch):
    fenced = ('```\n{"subjective": "s", "objective": "o", '
              '"assessment": "a", "plan": "p"}\n```')
    _fake_call(monkeypatch, fenced)
    note, _, _ = structure_note("transcript text")
    assert note.subjective == "s"


def test_invalid_json_raises_clear_parse_error(monkeypatch):
    _fake_call(monkeypatch, "this is not json at all")
    with pytest.raises(NoteStructuringError, match="not valid JSON"):
        structure_note("transcript text")


def test_json_missing_required_field_raises_clear_parse_error(monkeypatch):
    # Missing "plan" - valid JSON, invalid SoapNote.
    _fake_call(monkeypatch, '{"subjective": "s", "objective": "o", '
                            '"assessment": "a"}')
    with pytest.raises(NoteStructuringError, match="SoapNote schema"):
        structure_note("transcript text")


def test_json_array_instead_of_object_raises_clear_parse_error(monkeypatch):
    _fake_call(monkeypatch, '["s", "o", "a", "p"]')
    with pytest.raises(NoteStructuringError, match="not an object"):
        structure_note("transcript text")


def test_parse_error_preview_is_truncated(monkeypatch):
    long_garbage = "x" * 500
    _fake_call(monkeypatch, long_garbage)
    with pytest.raises(NoteStructuringError) as exc_info:
        structure_note("transcript text")
    assert "..." in str(exc_info.value)
    assert len(str(exc_info.value)) < 500
