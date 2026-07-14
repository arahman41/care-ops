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


# ---------- truncation: the bug the P1-4 harness surfaced ----------
# A response cut off at max_tokens used to surface as "Unterminated string at
# line 5", which reads like a prompt bug and is really an output-budget bug.
# It aborted the first full held-out run on ACI-Bench D2N101. Both the loud
# failure and the budget that prevents it are now pinned.

def test_a_truncated_response_raises_an_actionable_error(monkeypatch):
    import shared.llm as llm

    class _Resp:
        stop_reason = "max_tokens"
        content = []

    monkeypatch.setattr(llm._client.messages, "create",
                        lambda **kw: _Resp())

    with pytest.raises(llm.TruncatedResponseError, match="Raise max_tokens"):
        llm.call("structuring", system="s", user="u", max_tokens=10)


def test_the_structuring_budget_clears_the_longest_heldout_note():
    # The longest held-out reference note is ~1350 tokens. A generated SOAP
    # note, its JSON scaffolding, and high-effort reasoning all come out of the
    # same budget, so the ceiling needs real headroom over that.
    from services.intake.structure import MAX_TOKENS

    assert MAX_TOKENS >= 4000


# ---------- resampling an invalid JSON sample ----------
#
# Structuring is a reasoning call and samples, so its JSON is occasionally
# invalid. One encounter in the 120-note held-out run (ACI-Bench D2N150) came
# back with an unescaped quote mid-string, and because nothing retried, that
# single bad sample aborted the entire eval. Re-calling the same transcript
# parsed cleanly, which is what makes it a sampling defect rather than a prompt
# defect.
#
# The line these tests hold: retry the SERIALIZATION, never the CONTENT.

VALID = ('{"subjective": "s", "objective": "o", '
         '"assessment": "a", "plan": "p"}')

# An unescaped quote mid-string: the exact malformation seen on D2N150.
MALFORMED = ('{"subjective": "he felt a "pop" in his knee", "objective": "o", '
             '"assessment": "a", "plan": "p"}')


def _sequence_call(monkeypatch, responses: list[str]) -> list[str]:
    """Fake `call` returning each response in turn. The returned list counts calls."""
    calls: list[str] = []

    def fake(component, system, user, max_tokens=1200):
        calls.append(user)
        return responses[len(calls) - 1]

    monkeypatch.setattr("services.intake.structure.call", fake)
    return calls


def test_a_malformed_sample_is_resampled_and_a_valid_one_wins(monkeypatch):
    calls = _sequence_call(monkeypatch, [MALFORMED, VALID])
    note, _, _ = structure_note("transcript text")
    assert note.subjective == "s"
    assert len(calls) == 2


def test_a_valid_first_sample_is_not_resampled(monkeypatch):
    calls = _sequence_call(monkeypatch, [VALID, VALID])
    structure_note("transcript text")
    assert len(calls) == 1, "a clean parse must not cost a second call"


def test_resampling_is_bounded_and_a_persistently_broken_model_still_raises(
        monkeypatch):
    from services.intake.structure import MAX_JSON_ATTEMPTS

    calls = _sequence_call(monkeypatch, [MALFORMED] * MAX_JSON_ATTEMPTS)
    with pytest.raises(NoteStructuringError, match="attempts"):
        structure_note("transcript text")
    assert len(calls) == MAX_JSON_ATTEMPTS


def test_a_schema_violation_is_never_resampled(monkeypatch):
    """This is the integrity guard, and it is the reason the retry is safe.

    A response that parses but does not match SoapNote is the model answering
    the wrong question, not a serialization glitch. Resampling it would be
    rerolling until the note is one we like, and an accuracy number computed
    over notes that were retried until they looked good measures nothing. Only
    unparseable JSON may be resampled.
    """
    calls = _sequence_call(monkeypatch, ['{"subjective": "s"}', VALID])
    with pytest.raises(NoteStructuringError, match="SoapNote schema"):
        structure_note("transcript text")
    assert len(calls) == 1, "a schema miss must not be rerolled"
