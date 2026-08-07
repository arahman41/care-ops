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


# ---------- the intersection analysis set (Task 6.5) ----------

from governance.coding_benchmark import build_analysis_set, INTERSECTION_MIN  # noqa: E402


def test_analysis_set_is_the_intersection_of_parsed_notes():
    # note -> (arm_a_ok, arm_b_ok)
    per_note = {
        "D2N001": (True, True),
        "D2N002": (True, False),   # arm B failed
        "D2N003": (False, True),   # arm A failed
        "D2N004": (True, True),
    }
    a = build_analysis_set(per_note)
    assert a.ids == ["D2N001", "D2N004"]
    assert a.dropped_ids == ["D2N002", "D2N003"]


def test_void_threshold_is_108_of_120():
    assert INTERSECTION_MIN == 108     # 0.90 * 120


def test_intersection_below_the_floor_is_flagged_void():
    per_note = {f"n{i}": (True, i >= 20) for i in range(120)}  # 20 arm-B fails
    a = build_analysis_set(per_note)
    assert len(a.ids) == 100 and a.is_void is True


def test_intersection_at_the_floor_is_not_void():
    per_note = {f"n{i}": (True, i >= 12) for i in range(120)}  # 108 retained
    a = build_analysis_set(per_note)
    assert len(a.ids) == 108 and a.is_void is False


def test_void_is_judged_on_the_intersection_not_per_arm():
    # The failure this threshold exists to catch: each arm fails only 8% (under
    # any plausible per-arm threshold), but on DISJOINT notes, so the paired
    # intersection loses 16% and must void.
    per_note = {}
    for i in range(120):
        a_ok = not (i < 10)              # arm A fails notes 0-9
        b_ok = not (10 <= i < 20)        # arm B fails notes 10-19
        per_note[f"n{i}"] = (a_ok, b_ok)
    a = build_analysis_set(per_note)
    assert len(a.ids) == 100             # neither arm failed >10, but 20 lost
    assert a.is_void is True


def test_attrition_summary_reports_the_shape_of_the_loss():
    from governance.coding_benchmark import attrition_length_summary
    s = attrition_length_summary(dropped_lengths=[100, 200, 300],
                                 retained_lengths=[10, 20])
    assert s["dropped"]["n"] == 3 and s["dropped"]["median"] == 200
    assert s["retained"]["n"] == 2 and s["retained"]["median"] == 15
    assert s["dropped"]["max"] == 300 and s["retained"]["min"] == 10


def test_attrition_summary_handles_an_empty_side():
    from governance.coding_benchmark import attrition_length_summary
    s = attrition_length_summary(dropped_lengths=[], retained_lengths=[5])
    assert s["dropped"]["n"] == 0 and s["dropped"]["median"] is None


# ---------- tally, artifact, replay (Task 7.1) ----------

import json  # noqa: E402

from governance.coding_benchmark import (  # noqa: E402
    NoteTally, aggregate_tallies, build_committed_artifact, build_roster,
    replay_coding, tally_from_deduped,
)
from shared import vocab  # noqa: E402


def _tally(v, nf, un, c1=0, c2=0, c3=0, c4=0, itok=10, otok=20, lat=100):
    return NoteTally(verified=v, not_found=nf, unchecked=un,
                     cause1=c1, cause2=c2, cause3=c3, cause4=c4,
                     input_tokens=itok, output_tokens=otok, latency_ms=lat)


def _meta():
    return {"A": {"requested_model": "claude-sonnet-5",
                  "requested_effort": "xhigh",
                  "observed_model": "claude-sonnet-5-20260101"},
            "B": {"requested_model": "claude-opus-4-8",
                  "requested_effort": "high",
                  "observed_model": "claude-opus-4-8-20260101"}}


def _run_meta(vocab_version=None):
    return {"vocab_version": vocab_version or vocab.VOCAB_VERSION,
            "vocab_floor_version": "none", "price_table_ref": None,
            "split_digest": "d" * 64, "dataset_ref": "aci-bench-heldout-v1"}


def test_aggregate_tallies_matches_arm_summary_rates():
    tallies = [_tally(1, 1, 0), _tally(2, 0, 0)]     # pooled 3/4 verified
    agg = aggregate_tallies(tallies)
    assert agg["verified_rate"] == pytest.approx(75.0)
    assert agg["not_found_rate"] == pytest.approx(25.0)


def test_committed_artifact_carries_no_billing_codes(tmp_path):
    # Build a tiny run payload with a real code in the roster; assert the code
    # never appears in the committed artifact.
    committed = build_committed_artifact(
        arm_tallies={"A": {"D2N001": _tally(1, 1, 0, c1=1)},
                     "B": {"D2N001": _tally(2, 0, 0)}},
        agreement={"D2N001": 0.5}, strata={"D2N001": False},
        comparison={"branch_fired": "inconclusive"},
        run_meta=_run_meta(), arm_meta=_meta())
    blob = json.dumps(committed)
    assert "E11.9" not in blob and "E119" not in blob   # no codes leak


def test_replay_recomputes_rates_and_matches(tmp_path):
    committed = build_committed_artifact(
        arm_tallies={"A": {"n1": _tally(3, 1, 0, c1=1)},
                     "B": {"n1": _tally(4, 0, 0)}},
        agreement={"n1": 0.8}, strata={"n1": False},
        comparison={"branch_fired": "inconclusive"},
        run_meta=_run_meta(), arm_meta=_meta())
    path = tmp_path / "coding_run.json"
    path.write_text(json.dumps(committed), encoding="utf-8")
    out = replay_coding(path)
    assert out["A"]["verified_rate"] == pytest.approx(75.0)


def test_replay_hard_errors_on_vocab_version_mismatch(tmp_path):
    committed = build_committed_artifact(
        arm_tallies={"A": {"n1": _tally(3, 1, 0)}, "B": {"n1": _tally(4, 0, 0)}},
        agreement={"n1": 1.0}, strata={"n1": False}, comparison={},
        run_meta=_run_meta("STALE PIN"), arm_meta=_meta())
    path = tmp_path / "c.json"
    path.write_text(json.dumps(committed), encoding="utf-8")
    with pytest.raises(ValueError, match="vocab_version"):
        replay_coding(path)


def test_replay_hard_errors_on_a_tampered_stored_rate(tmp_path):
    committed = build_committed_artifact(
        arm_tallies={"A": {"n1": _tally(3, 1, 0)}, "B": {"n1": _tally(4, 0, 0)}},
        agreement={"n1": 1.0}, strata={"n1": False}, comparison={},
        run_meta=_run_meta(), arm_meta=_meta())
    committed["arms"]["A"]["verified_rate"] = 99.0     # tamper
    path = tmp_path / "c.json"
    path.write_text(json.dumps(committed), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match|recompute"):
        replay_coding(path)


def test_replay_hard_errors_on_tampered_token_counts(tmp_path):
    # Cost is measured-once, so replay cannot recompute it from responses. It
    # CAN verify the stored aggregate against the stored per-note values.
    committed = build_committed_artifact(
        arm_tallies={"A": {"n1": _tally(3, 1, 0)}, "B": {"n1": _tally(4, 0, 0)}},
        agreement={"n1": 1.0}, strata={"n1": False}, comparison={},
        run_meta=_run_meta(), arm_meta=_meta())
    committed["arms"]["A"]["output_tokens"] = 999999
    path = tmp_path / "c.json"
    path.write_text(json.dumps(committed), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        replay_coding(path)


def test_roster_is_a_separate_structure_from_the_artifact():
    roster = build_roster([{"encounter_id": "n1", "arm": "A",
                            "systems_seen": ["ICD-10"], "code": "E119",
                            "model_description": "diabetes", "auto_cause": 1,
                            "adjudication": ""}])
    assert roster["columns"][0] == "encounter_id"
    assert roster["rows"][0]["code"] == "E119"


# The plan's executor notes require this: aggregate_arm (Chunk 2) and
# aggregate_tallies (Chunk 7) are INDEPENDENT implementations of the same
# pooled-rate math, kept as a double-entry check on headline-adjacent
# arithmetic. Two independent implementations agreeing is the correctness
# signal; this test is what makes them stay agreed.
def test_the_two_aggregation_paths_agree(monkeypatch):
    from governance.coding_metrics import aggregate_arm, dedupe_note
    from shared.schemas import CodeSuggestion, CodingOutput

    def _out(*pairs):
        codes = [CodeSuggestion(system=s, code=c, description="d",
                                vocabulary_status=vocab.classify(s, c))
                 for s, c in pairs]
        return CodingOutput(codes=codes, confidence=0.9,
                            vocabulary_version=vocab.VOCAB_VERSION)

    notes = [
        _out(("ICD-10", "E11.9"), ("ICD-10", "M9999"), ("CPT", "99213")),
        _out(("ICD-10", "I10"), ("ICD-10", "E11.9"), ("ICD-10", "NONE")),
        _out(("CPT", "99213"),),
        _out(("ICD-10", "J18.9"), ("ICD-10", "99213")),
    ]
    deduped = [dedupe_note(n) for n in notes]

    summary = aggregate_arm(deduped)
    tallies = [tally_from_deduped(d, input_tokens=1, output_tokens=2,
                                  latency_ms=3) for d in deduped]
    agg = aggregate_tallies(tallies)

    assert agg["verified_rate"] == pytest.approx(summary.verified_rate, abs=1e-9)
    assert agg["not_found_rate"] == pytest.approx(summary.not_found_rate, abs=1e-9)
    assert agg["unchecked_share"] == pytest.approx(summary.unchecked_share, abs=1e-9)
    assert agg["pessimistic_verified_rate"] == pytest.approx(
        summary.pessimistic_verified_rate, abs=1e-9)
    assert agg["codes_per_note"] == pytest.approx(summary.codes_per_note, abs=1e-9)
    assert agg["n_checkable"] == summary.checkable


def test_assemble_run_restricts_everything_to_the_intersection():
    from governance.coding_benchmark import ArmNoteResult, assemble_run
    from shared.schemas import CodeSuggestion, CodingOutput

    def _co(*pairs):
        codes = [CodeSuggestion(system=s, code=c, description="d",
                                vocabulary_status=vocab.classify(s, c))
                 for s, c in pairs]
        return CodingOutput(codes=codes, confidence=0.9,
                            vocabulary_version=vocab.VOCAB_VERSION)

    def _res(out):
        return ArmNoteResult(out, "m-1", (10, 20), 100,
                             None if out is not None else "parse: boom")

    a = {"n1": _res(_co(("ICD-10", "E11.9"), ("ICD-10", "M9999"))),
         "n2": _res(_co(("ICD-10", "I10"))),
         "n3": _res(None)}                       # arm A failed n3
    b = {"n1": _res(_co(("ICD-10", "E11.9"))),
         "n2": _res(None),                       # arm B failed n2
         "n3": _res(_co(("ICD-10", "I10")))}
    strata = {"n1": False, "n2": True, "n3": False}

    run = assemble_run(a, b, strata)
    assert run.analysis.ids == ["n1"]            # only n1 survived both arms
    assert set(run.arm_tallies["A"]) == {"n1"}
    assert set(run.arm_tallies["B"]) == {"n1"}
    assert set(run.agreement) == {"n1"}
    assert set(run.strata) == {"n1"}
    # n1: arm A {E119, M9999}, arm B {E119} -> Jaccard 1/2
    assert run.agreement["n1"] == pytest.approx(0.5)
    # NotePair ordering follows analysis.ids, arm A has 1 not_found of 2 checkable
    assert len(run.note_pairs) == 1
    assert run.note_pairs[0].nf_a == 1 and run.note_pairs[0].checkable_a == 2
    assert run.note_pairs[0].nf_b == 0 and run.note_pairs[0].checkable_b == 1
    # tokens ride through from the ArmNoteResult, for cost accounting
    assert run.arm_tallies["A"]["n1"].input_tokens == 10
    assert run.arm_tallies["A"]["n1"].output_tokens == 20


def test_assemble_run_note_pairs_align_with_analysis_ids_order():
    from governance.coding_benchmark import ArmNoteResult, assemble_run
    from shared.schemas import CodeSuggestion, CodingOutput

    def _co(code):
        return CodingOutput(
            codes=[CodeSuggestion(system="ICD-10", code=code, description="d",
                                  vocabulary_status=vocab.classify("ICD-10", code))],
            confidence=0.9, vocabulary_version=vocab.VOCAB_VERSION)

    # Insertion order deliberately not sorted, to prove the pairing follows the
    # SORTED analysis ids and cannot silently pair note i of arm A with note j
    # of arm B.
    a = {"n3": ArmNoteResult(_co("M9999"), "m", (1, 1), 1, None),
         "n1": ArmNoteResult(_co("E11.9"), "m", (1, 1), 1, None)}
    b = {"n3": ArmNoteResult(_co("E11.9"), "m", (1, 1), 1, None),
         "n1": ArmNoteResult(_co("M9999"), "m", (1, 1), 1, None)}
    run = assemble_run(a, b, {"n1": False, "n3": False})
    assert run.analysis.ids == ["n1", "n3"]
    # n1: A verified (nf 0), B not_found (nf 1). n3 is the mirror image.
    assert (run.note_pairs[0].nf_a, run.note_pairs[0].nf_b) == (0, 1)
    assert (run.note_pairs[1].nf_a, run.note_pairs[1].nf_b) == (1, 0)


def test_tally_from_deduped_causes_sum_to_not_found():
    # Every not_found code lands in exactly one cause, which is what lets
    # _rates_from_sums derive cause1 as the residual.
    from governance.coding_metrics import dedupe_note
    from shared.schemas import CodeSuggestion, CodingOutput
    codes = [CodeSuggestion(system=s, code=c, description="d",
                            vocabulary_status=vocab.classify(s, c))
             for s, c in [("ICD-10", "M9999"), ("ICD-10", "NONE"),
                          ("ICD-10", "99213"), ("ICD-10", "E11.9")]]
    d = dedupe_note(CodingOutput(codes=codes, confidence=0.5,
                                 vocabulary_version=vocab.VOCAB_VERSION))
    t = tally_from_deduped(d, input_tokens=1, output_tokens=1, latency_ms=1)
    assert t.cause1 + t.cause2 + t.cause3 + t.cause4 == t.not_found
    assert t.not_found == 3 and t.verified == 1
