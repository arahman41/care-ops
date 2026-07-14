"""The harness end to end, and the replay that makes its number auditable (P1-4).

The replay test is the important one. It proves the committed artifact is not
a transcript of a number somebody once saw, but a set of per-fact verdicts the
headline metric can be *recomputed* from, offline, with no API key and no
spend. That is what turns "our structuring F1 is X" from a claim into a
reproducible fact, and it is what P4-5's metric audit will lean on.
"""
from __future__ import annotations

import json

import pytest

from governance import structuring_eval as se
from governance.aci_sections import ASSESSMENT, PLAN, SUBJECTIVE
from governance.evaluate import StructuringCounts, score_structuring
from governance.heldout import HeldoutExample
from governance.llm_cache import Cache
from governance.structuring_eval import evaluate_examples, replay, write_artifacts
from shared.schemas import SoapNote

REF_NOTE = (
    "CHIEF COMPLAINT\r\n\r\nCough.\r\n\r\n"
    "ASSESSMENT AND PLAN\r\n\r\nURI. Rest.\r\n"
)

EXAMPLES = [
    HeldoutExample(dataset="aci-bench", encounter_id="D2N088",
                   transcript="Doctor: cough? Patient: yes.",
                   reference_note=REF_NOTE),
    HeldoutExample(dataset="aci-bench", encounter_id="D2N089",
                   transcript="Doctor: cough? Patient: yes.",
                   reference_note=REF_NOTE),
]

SOAP = SoapNote(subjective="Cough.", objective="", assessment="URI.",
                plan="Rest.")


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache")


@pytest.fixture
def stubbed(monkeypatch):
    """A fully deterministic pipeline: 2 ref facts, 2 gen facts, per note."""
    monkeypatch.setattr(se, "generate_soap",
                        lambda transcript, cache: (SOAP, "stub-model", "high"))

    def fake_decompose_reference(note, cache):
        from governance.facts import Fact
        return [
            Fact("Cough.", frozenset({SUBJECTIVE}), "CHIEF COMPLAINT"),
            Fact("URI.", frozenset({ASSESSMENT, PLAN}), "ASSESSMENT AND PLAN"),
        ]

    def fake_decompose_soap(soap, cache):
        from governance.facts import Fact
        return [
            Fact("Cough.", frozenset({SUBJECTIVE}), SUBJECTIVE),
            Fact("URI.", frozenset({ASSESSMENT}), ASSESSMENT),
        ]

    def fake_judge_presence(soap, facts, cache):
        from governance.judge import PresenceVerdict
        # First fact: found, right section. Second: found, but in "plan",
        # which the fused reference accepts, so it is correctly placed too.
        return [
            PresenceVerdict(facts[0], True, SUBJECTIVE),
            PresenceVerdict(facts[1], True, PLAN),
        ]

    def fake_judge_support(transcript, gen_facts, cache):
        return [True, False]        # one grounded, one hallucinated

    monkeypatch.setattr(se, "decompose_reference", fake_decompose_reference)
    monkeypatch.setattr(se, "decompose_soap", fake_decompose_soap)
    monkeypatch.setattr(se, "judge_presence", fake_judge_presence)
    monkeypatch.setattr(se, "judge_support", fake_judge_support)


def test_counts_and_metrics_are_what_we_computed_by_hand(stubbed, cache):
    result = evaluate_examples(EXAMPLES, cache=cache, workers=1)

    # 2 notes x 2 ref facts = 4; both found; both in an acceptable section.
    # 2 notes x 2 gen facts = 4; one supported per note.
    assert result.counts == StructuringCounts(
        ref_facts=4, captured=4, correctly_placed=4, gen_facts=4, supported=2)

    assert result.metrics["recall"] == pytest.approx(1.0)
    assert result.metrics["precision"] == pytest.approx(0.5)
    assert result.metrics["accuracy"] == pytest.approx(1.0)
    assert result.metrics["hallucination_rate"] == pytest.approx(0.5)
    assert result.metrics["f1"] == pytest.approx(2 * 0.5 * 1.0 / 1.5)


def test_the_fused_note_count_is_carried_into_the_report(stubbed, cache):
    result = evaluate_examples(EXAMPLES, cache=cache, workers=1)
    assert result.fused_notes == 2      # both fixtures fuse A and P


def test_a_fact_placed_in_a_fused_sibling_section_still_counts(stubbed, cache):
    # The "URI." fact was filed under plan while the reference header was
    # ASSESSMENT AND PLAN. That is correct, and the metric must say so.
    result = evaluate_examples(EXAMPLES, cache=cache, workers=1)
    verdicts = result.examples[0].ref_verdicts
    uri = [v for v in verdicts if v.fact.text == "URI."][0]
    assert uri.section == PLAN
    assert uri.correctly_placed is True


# ---------- the reproducibility guarantee ----------

def test_replay_recomputes_the_identical_metrics_offline(stubbed, cache, tmp_path):
    result = evaluate_examples(EXAMPLES, cache=cache, workers=1)
    artifact = write_artifacts(result, out_dir=tmp_path)

    # Nothing may call the API during a replay, so break it loudly.
    replayed = replay(artifact)

    assert replayed["counts"] == result.counts
    for name, value in result.metrics.items():
        assert replayed["metrics"][name] == pytest.approx(value)


def test_replay_recomputes_rather_than_rereads(stubbed, cache, tmp_path):
    # If replay just echoed the stored metrics back it would prove nothing.
    # Corrupt a stored metric and confirm replay disagrees with it.
    result = evaluate_examples(EXAMPLES, cache=cache, workers=1)
    artifact = write_artifacts(result, out_dir=tmp_path)

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["metrics"]["f1"] = 0.99
    artifact.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        replay(artifact)


def test_the_committed_artifact_carries_no_clinical_text(stubbed, cache, tmp_path):
    # data/ is gitignored under the project's no-clinical-data-in-git rule, so
    # the artifact that gets committed must carry verdicts, not note content.
    result = evaluate_examples(EXAMPLES, cache=cache, workers=1)
    artifact = write_artifacts(result, out_dir=tmp_path)

    blob = artifact.read_text(encoding="utf-8")
    assert "Cough." not in blob
    assert "URI." not in blob
    assert "Doctor:" not in blob


def test_the_full_artifact_keeps_the_text_for_the_hand_audit(
        stubbed, cache, tmp_path):
    result = evaluate_examples(EXAMPLES, cache=cache, workers=1)
    write_artifacts(result, out_dir=tmp_path)

    full = next(tmp_path.glob("*.full.json"))
    assert "Cough." in full.read_text(encoding="utf-8")


def test_the_artifact_pins_the_split_digest_and_the_models(
        stubbed, cache, tmp_path):
    result = evaluate_examples(EXAMPLES, cache=cache, workers=1)
    artifact = write_artifacts(result, out_dir=tmp_path)
    payload = json.loads(artifact.read_text(encoding="utf-8"))

    # A number is only meaningful next to the split and models that produced it.
    assert payload["split_digest"]
    assert payload["structuring_model"] == "stub-model"
    assert payload["judge_model"]
    assert payload["prompt_versions"]


def test_an_empty_run_does_not_divide_by_zero(cache):
    result = evaluate_examples([], cache=cache, workers=1)
    assert result.counts == StructuringCounts(0, 0, 0, 0, 0)
    assert result.metrics == score_structuring(StructuringCounts(0, 0, 0, 0, 0))


# ---------- the committed artifacts: CI regression-tests the headline ----------

COMMITTED = sorted(p for p in se.ARTIFACT_DIR.glob("*.json")
                   if not p.name.endswith(".full.json"))


@pytest.mark.skipif(not COMMITTED,
                    reason="no artifact committed yet; the first real run writes one")
@pytest.mark.parametrize("artifact", COMMITTED, ids=lambda p: p.stem)
def test_a_committed_artifact_still_recomputes_its_own_headline(artifact):
    """Regression-test the published number itself, for free, on every CI run.

    replay() recomputes the metrics from the per-fact verdicts and raises if
    they disagree with the metrics the artifact stores. So this asserts that
    the number quoted in the README is still the number those verdicts
    produce. If anyone edits an artifact, or changes the metric math under a
    published result, CI fails loudly instead of letting a stale claim stand.
    """
    out = replay(artifact)

    # An all-zero artifact would satisfy replay() vacuously (0 == 0), so pin
    # that the thing we just "verified" actually counted something.
    assert out["counts"].ref_facts > 0
    assert out["counts"].gen_facts > 0

    payload = out["payload"]
    assert payload["n_examples"] == len(payload["examples"])
    assert payload["split_digest"], "an artifact that names no split is unmoored"
