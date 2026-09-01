"""P3-2: a window must be a measurement, not a replay of an earlier one.

`cache_key` covers the model, the prompt version and the payload but NOT the
window, and P3-1 defined a window as a point in time with the generation
configuration held FIXED. Two windows are therefore the same cache key by
construction. Measured on 2026-08-31, before any of this existed: all 120
ACI-Bench held-out structuring calls were already cached, so a second window
run that day would have replayed July's notes, reproduced its metrics
bit-identically, and filed them under today's date. `replay()` would have
passed, P3-1's guard would have passed, and CI would have been green.

Two independent guards close that, and they fail for different reasons so
neither can mask the other:

  guard 1, causal      a window served from cache is refused at ingest
  guard 2, symptomatic two windows may not carry identical counts

Needs no dataset and no database except where marked.
"""
from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from governance.eval_runner import (
    ReplayedWindowError,
    prepare_artifact,
    record_scored,
)
from governance import structuring_eval as se
from governance.aci_sections import ASSESSMENT, PLAN, SUBJECTIVE
from governance.heldout import ACI_DATASET_REF
from governance.judge import PresenceVerdict
from governance.llm_cache import Cache, cache_key
from governance.structuring_eval import (
    ARTIFACT_DIR,
    locked_digest,
    run_cost,
    window_cache,
    write_artifacts,
)
from shared.config import settings
from shared.db import get_conn
from shared.llm import recording_usage
from shared.schemas import SoapNote

ACI = ARTIFACT_DIR / "structuring_aci-bench-heldout-v1_20260714T032403Z.json"

FRESH = {"window": {"hits": {"structure": 0}, "misses": {"structure": 120}}}
REPLAYED = {"window": {"hits": {"structure": 120}, "misses": {}}}

SOAP = SoapNote(subjective="Cough.", objective="", assessment="URI.",
                plan="Rest.")


def _artifact(tmp_path: Path, cache_stats: dict, n: int = 2) -> Path:
    """A small, valid, SYNTHETIC window artifact.

    Deliberately not a copy of the committed July artifact. Rows 7 and 8 hold
    July's measurement permanently, so filing that artifact under any other
    label is a genuine duplicate and guard 2 refuses it, correctly. A test
    that needs a fileable window therefore has to be a different measurement,
    not a relabelled copy of the real one.

    `n` scales the counts, so two tests can produce deliberately identical or
    deliberately distinct measurements.
    """
    from governance.facts import Fact

    verdicts = [
        PresenceVerdict(Fact("Cough.", frozenset({SUBJECTIVE}), "CC"),
                        True, SUBJECTIVE),
        PresenceVerdict(Fact("URI.", frozenset({ASSESSMENT, PLAN}), "AP"),
                        True, ASSESSMENT),
    ]
    examples = [
        se.ExampleResult(encounter_id=f"SYN{i:03d}", fused=False, soap=SOAP,
                         model="claude-sonnet-5", effort="high",
                         ref_verdicts=verdicts,
                         gen_fact_texts=["Cough.", "URI."],
                         gen_supported=[True, True])
        for i in range(n)
    ]
    result = se.RunResult(
        dataset_ref=ACI_DATASET_REF, structuring_model="claude-sonnet-5",
        structuring_effort="high", split_digest=locked_digest(),
        examples=examples, cache_stats=cache_stats)

    tmp_path.mkdir(parents=True, exist_ok=True)
    return write_artifacts(result, out_dir=tmp_path)


# ---------- the cache namespace: the mechanism, isolated ----------

def test_a_window_cache_does_not_see_another_windows_entries(tmp_path,
                                                             monkeypatch):
    """The isolation itself. Without it every later window is a replay."""
    monkeypatch.setattr("governance.structuring_eval.WINDOW_CACHE_DIR",
                        tmp_path / "windows")
    key = cache_key("structure", "claude-sonnet-5", "high|abc|max8000", "note")

    window_cache("2026-07-w2").put(key, '{"subjective": "july"}')

    assert window_cache("2026-07-w2").get(key, "structure") is not None
    assert window_cache("2026-08-w5").get(key, "structure") is None


def test_the_same_window_label_reuses_its_own_cache(tmp_path, monkeypatch):
    """Re-running one window must stay warm, or a window is not reproducible."""
    monkeypatch.setattr("governance.structuring_eval.WINDOW_CACHE_DIR",
                        tmp_path / "windows")
    key = cache_key("structure", "m", "v", "note")
    window_cache("w").put(key, "value")

    assert window_cache("w").get(key, "structure") == "value"


def test_the_cache_counts_hits_and_misses_per_task(tmp_path):
    cache = Cache(tmp_path)
    key = cache_key("structure", "m", "v", "note")

    assert cache.get(key, "structure") is None      # miss
    cache.put(key, "value")
    assert cache.get(key, "structure") == "value"   # hit
    cache.get(cache_key("judge", "m", "v", "x"), "presence")  # miss

    stats = cache.stats()
    assert stats["hits"] == {"structure": 1}
    assert stats["misses"] == {"structure": 1, "presence": 1}
    assert stats["total_hits"] == 1 and stats["total_misses"] == 2


# ---------- guard 1: a window served from cache is refused ----------

def test_an_artifact_served_entirely_from_cache_is_refused(tmp_path):
    artifact = _artifact(tmp_path, REPLAYED)

    with pytest.raises(ReplayedWindowError, match="served entirely from cache"):
        prepare_artifact(agent_name="note_structuring",
                         artifact_path=artifact, window_label="2026-08-w5")


def test_a_freshly_generated_artifact_is_accepted(tmp_path):
    artifact = _artifact(tmp_path, FRESH)

    scored = prepare_artifact(agent_name="note_structuring",
                              artifact_path=artifact, window_label="2026-08-w5")

    assert scored.metrics["f1"] > 0


def test_a_partially_cached_rerun_is_accepted(tmp_path):
    """A run resumed after a crash is half warm and wholly legitimate. Guard 1
    targets the all-hits-no-misses signature, not any cache use at all."""
    artifact = _artifact(tmp_path, {
        "window": {"hits": {"structure": 60}, "misses": {"structure": 60}}})

    scored = prepare_artifact(agent_name="note_structuring",
                              artifact_path=artifact, window_label="w")

    assert scored.n_examples == 2


def test_an_artifact_from_before_p3_2_is_exempt():
    """Window 1 carries no cache_stats and genuinely was generated. Absence of
    evidence must not be read as evidence of a replay, or the July windows
    become unfileable."""
    scored = prepare_artifact(agent_name="note_structuring",
                              artifact_path=ACI, window_label="v1")

    assert scored.provenance["cache_stats"] is None


# ---------- the mutation check the spec requires ----------

def test_guard_1_actually_detects_the_replay_signature(tmp_path):
    """A guard that passes whether or not the isolation exists is not a guard.

    This is the P2-6 reducer check in a different costume: assert that the
    refusal is caused by the replay signature specifically, by showing the same
    artifact passes once that signature is removed and fails once it is put
    back. If a future edit neuters _assert_freshly_generated, the first half
    of this test still passes and the second half fails.
    """
    replayed = _artifact(tmp_path / "a", REPLAYED)
    fresh = _artifact(tmp_path / "b", FRESH)

    prepare_artifact(agent_name="note_structuring", artifact_path=fresh,
                     window_label="w")      # must not raise

    with pytest.raises(ReplayedWindowError):
        prepare_artifact(agent_name="note_structuring",
                         artifact_path=replayed, window_label="w")


# ---------- cost accounting ----------

def test_usage_is_not_recorded_unless_asked_for():
    from shared import llm
    assert llm._usage_recorder is None


def test_the_usage_recorder_totals_calls_across_models():
    with recording_usage() as usage:
        usage._record("claude-sonnet-5", "claude-sonnet-5", 100, 200)
        usage._record("claude-sonnet-5", "claude-sonnet-5", 50, 25)
        usage._record("claude-haiku-4-5-20251001", "claude-haiku-4-5", 10, 5)

        totals = usage.as_dict()

    assert usage.calls == 3
    assert totals["claude-sonnet-5"]["calls"] == 2
    assert totals["claude-sonnet-5"]["input_tokens"] == 150
    assert totals["claude-sonnet-5"]["output_tokens"] == 225
    assert totals["claude-haiku-4-5-20251001"]["observed_models"] == [
        "claude-haiku-4-5"]


def test_nested_usage_recording_is_refused():
    """Nesting would double-count into both recorders and understate nothing
    visibly, which is the worst direction for a cost report to be wrong in."""
    with recording_usage():
        with pytest.raises(RuntimeError, match="already active"):
            with recording_usage():
                pass


def test_the_recorder_is_cleared_even_if_the_block_raises():
    from shared import llm

    with pytest.raises(ValueError):
        with recording_usage():
            raise ValueError("boom")

    assert llm._usage_recorder is None


def test_a_run_with_an_unpriced_model_reports_no_cost_rather_than_a_low_one():
    """pricing.py's own rule: an understated cost looks like an answer."""
    assert run_cost({"some-unpriced-model":
                     {"input_tokens": 1000, "output_tokens": 1000}}) is None


def test_run_cost_prices_the_judge_now_that_haiku_is_in_the_table():
    """Haiku 4.5 was absent from pricing.json until P3-2, which is why the
    structuring harness could never cost itself: the judge is Haiku."""
    cost = run_cost({"claude-haiku-4-5-20251001":
                     {"input_tokens": 1_000_000, "output_tokens": 1_000_000}})

    assert cost == pytest.approx(6.0)      # $1 in + $5 out


def test_sonnet_5_is_priced_at_the_rate_that_actually_took_effect():
    """pricing.json recorded $3/$15 with a note that $2/$10 was introductory
    'through 2026-08-31'. The published table says the increase was cancelled
    and $2/$10 is now standard, so the old entry priced a rate that never
    existed. A committed price table goes stale silently, and a cost-based
    routing decision sits on top of this one."""
    cost = run_cost({"claude-sonnet-5":
                     {"input_tokens": 1_000_000, "output_tokens": 1_000_000}})

    assert cost == pytest.approx(12.0)     # $2 in + $10 out


# ---------- guard 2, against a real database ----------

def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="no reachable Postgres")

W1, W2 = "p3-2-test-window-one", "p3-2-test-window-two"


@pytest.fixture
def clean_windows():
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM eval_runs WHERE window_label IN (%s, %s)",
                     (W1, W2))


@needs_db
def test_a_second_window_with_identical_counts_is_refused(tmp_path,
                                                          clean_windows):
    """The symptomatic guard. Across 120 encounters and 6,550 reference facts,
    independent generation does not reproduce all five tallies exactly."""
    artifact = _artifact(tmp_path, FRESH)

    first = prepare_artifact(agent_name="note_structuring",
                             artifact_path=artifact, window_label=W1)
    record_scored(first)

    second = prepare_artifact(agent_name="note_structuring",
                              artifact_path=artifact, window_label=W2)
    with pytest.raises(ReplayedWindowError, match="counts identical"):
        record_scored(second)


@needs_db
def test_refiling_the_same_window_label_stays_legal(tmp_path, clean_windows):
    """Guard 2 is scoped to a DIFFERENT label on purpose: re-filing is by
    definition the same measurement under the same label, which is exactly
    what scripts/refile_eval_run.py does and must keep doing."""
    artifact = _artifact(tmp_path, FRESH)
    scored = prepare_artifact(agent_name="note_structuring",
                              artifact_path=artifact, window_label=W1)

    record_scored(scored)
    record_scored(scored)      # must not raise

    with get_conn() as conn:
        n = conn.execute("SELECT count(*) FROM eval_runs WHERE window_label = %s",
                         (W1,)).fetchone()[0]
    assert n == 2


@needs_db
def test_a_refused_second_window_inserts_nothing(tmp_path, clean_windows):
    artifact = _artifact(tmp_path, FRESH)
    record_scored(prepare_artifact(agent_name="note_structuring",
                                   artifact_path=artifact, window_label=W1))

    def count() -> int:
        with get_conn() as conn:
            return conn.execute(
                "SELECT count(*) FROM eval_runs WHERE window_label = %s",
                (W2,)).fetchone()[0]

    before = count()
    with pytest.raises(ReplayedWindowError):
        record_scored(prepare_artifact(agent_name="note_structuring",
                                       artifact_path=artifact,
                                       window_label=W2))
    assert count() == before == 0
