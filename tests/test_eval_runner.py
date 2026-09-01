"""P3-1: filing a committed artifact as one window in eval_runs.

The replay-based tests need neither a dataset nor a database. The redacted
artifacts are tracked in git and carry no clinical text, so CI regression-tests
the ingest path for free. The round trips at the bottom are behind needs_db,
following the module-local skipif idiom in tests/test_registry.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest

from governance.eval_runner import (
    CONFIG_FIELDS,
    GenerationConfig,
    config_from_artifact,
    prepare_artifact,
    score_artifact,
)
from governance.evaluate import (
    UnknownAgentError,
    UnscoreableAgentError,
    record_eval_run,
)
from governance.heldout import SplitDriftError
from governance.structuring_eval import ARTIFACT_DIR
from shared.config import settings
from shared.db import get_conn

ACI = ARTIFACT_DIR / "structuring_aci-bench-heldout-v1_20260714T032403Z.json"
PRIMOCK = ARTIFACT_DIR / "structuring_primock57-heldout-v1_20260714T093650Z.json"

# P1-4's published headline. Asserted exactly, not approximately: replay()
# recomputes from the per-fact verdicts, so any drift here means the number
# in the README stopped being the number the verdicts produce.
ACI_F1 = 0.8685633622463043
ACI_MEASURED_AT = datetime.fromisoformat("2026-07-14T03:24:03.340016+00:00")

BASE = GenerationConfig(model="claude-sonnet-5", effort="high",
                        prompt_hash="b7b42093e9a7", max_tokens=8000)


def _mutated(tmp_path: Path, source: Path, **changes) -> Path:
    """Copy an artifact with top-level keys changed or dropped (value None)."""
    payload = json.loads(source.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    out = tmp_path / source.name
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


# ---------- GenerationConfig: what makes two windows comparable ----------

@pytest.mark.parametrize("field,other", [
    ("model", "claude-opus-4-8"),
    ("effort", "xhigh"),
    ("prompt_hash", "0000deadbeef"),
    ("max_tokens", 1200),
])
def test_differing_fields_detects_each_field_independently(field, other):
    changed = GenerationConfig(**{**BASE.__dict__, field: other})

    assert BASE.differing_fields(changed) == (field,)
    assert changed.differing_fields(BASE) == (field,)


def test_identical_configs_differ_in_nothing():
    assert BASE.differing_fields(GenerationConfig(**BASE.__dict__)) == ()


def test_an_unrecorded_max_tokens_on_one_side_counts_as_differing():
    """None means "not recorded by that harness", not 8000. Treating it as
    equal would let P3-3 certify two windows as comparable when one of them
    may have been generated under a different output cap, and a cap change
    silently rewrites long notes. The ambiguity has to be visible in data."""
    unrecorded = GenerationConfig(**{**BASE.__dict__, "max_tokens": None})

    assert unrecorded.differing_fields(BASE) == ("max_tokens",)
    assert BASE.differing_fields(unrecorded) == ("max_tokens",)


def test_two_equally_unprovenanced_configs_do_not_differ():
    a = GenerationConfig(**{**BASE.__dict__, "max_tokens": None})
    b = GenerationConfig(**{**BASE.__dict__, "max_tokens": None})

    assert a.differing_fields(b) == ()


def test_config_fields_covers_every_field_on_the_dataclass():
    """If a fifth field is added and not listed, differing_fields would ignore
    it and two incomparable runs would be certified comparable."""
    assert set(CONFIG_FIELDS) == set(BASE.__dict__)


# ---------- reading the configuration back out of a committed artifact ----------

def test_config_from_the_july_aci_artifact():
    config = config_from_artifact(json.loads(ACI.read_text(encoding="utf-8")))

    assert config.model == "claude-sonnet-5"
    assert config.effort == "high"
    assert config.prompt_hash == "b7b42093e9a7"
    # Permanently unknown, and recorded as unknown. The harness of the day did
    # not write it down, so nothing may assert it was 8000 (P3-1 spec §4).
    assert config.max_tokens is None


def test_config_reads_max_tokens_when_the_artifact_records_it(tmp_path):
    """Every artifact written from P3-1 onward carries it. This is the forward
    half of the gap: null backwards, real going forward."""
    artifact = _mutated(tmp_path, ACI, structuring_max_tokens=8000)

    config = config_from_artifact(
        json.loads(artifact.read_text(encoding="utf-8")))

    assert config.max_tokens == 8000


# ---------- preparing the row ----------

def test_the_july_aci_artifact_replays_to_its_published_headline():
    scored = prepare_artifact(agent_name="note_structuring",
                              artifact_path=ACI, window_label="2026-07-w2")

    assert scored.metrics["f1"] == ACI_F1
    assert scored.n_examples == 120
    assert scored.dataset_ref == "aci-bench-heldout-v1"
    assert scored.model == "claude-sonnet-5"
    assert scored.model_effort == "high"


def test_the_row_carries_the_measurement_time_not_now():
    """The whole reason created_at is written explicitly. Backfilled today
    under the column default, July's run would sit at the newest end of the
    trend and P3-3 would read any drift backwards."""
    scored = prepare_artifact(agent_name="note_structuring",
                              artifact_path=ACI, window_label="2026-07-w2")

    assert scored.measured_at == ACI_MEASURED_AT
    assert scored.measured_at < datetime.now(timezone.utc)


def test_the_primock_row_declines_placement_while_keeping_the_real_metrics():
    """replay() forces accuracy back to None for PriMock57, because placement
    is not scorable against an unsectioned note. That None has to survive all
    the way into the row, or the 1.0 the harness refused to publish reappears
    one layer further down, in the table a dashboard reads."""
    scored = prepare_artifact(agent_name="note_structuring",
                              artifact_path=PRIMOCK, window_label="2026-07-w2")

    assert scored.metrics["accuracy"] is None
    assert scored.metrics["f1"] > 0
    assert scored.metrics["precision"] > 0
    assert scored.metrics["recall"] > 0
    assert scored.provenance["placement_scored"] is False


def test_provenance_records_the_whole_generation_configuration():
    scored = prepare_artifact(agent_name="note_structuring",
                              artifact_path=ACI, window_label="2026-07-w2")

    generation = scored.provenance["generation"]
    assert set(generation) == set(CONFIG_FIELDS)
    assert generation["max_tokens"] is None
    assert scored.provenance["split_digest"]
    assert scored.provenance["artifact"] == ACI.name


# ---------- refusals ----------

def test_an_unscoreable_agent_is_refused_before_the_artifact_is_even_read():
    """Ordering, proved rather than asserted: the path does not exist, so
    anything that touched the file first would raise FileNotFoundError."""
    with pytest.raises(UnscoreableAgentError, match="gold billing codes"):
        prepare_artifact(agent_name="coding",
                         artifact_path=Path("does-not-exist.json"),
                         window_label="w")


def test_an_unknown_agent_is_refused_as_unknown():
    with pytest.raises(UnknownAgentError):
        prepare_artifact(agent_name="note_structurin",
                         artifact_path=Path("does-not-exist.json"),
                         window_label="w")


def test_an_artifact_scored_on_another_dataset_is_refused(tmp_path):
    artifact = _mutated(tmp_path, ACI, dataset_ref="some-other-set-v9")

    with pytest.raises(ValueError, match="some-other-set-v9"):
        prepare_artifact(agent_name="note_structuring",
                         artifact_path=artifact, window_label="w")


def test_an_artifact_with_no_measurement_time_is_refused(tmp_path):
    artifact = _mutated(tmp_path, ACI, created_at=None)

    with pytest.raises(ValueError, match="no created_at"):
        prepare_artifact(agent_name="note_structuring",
                         artifact_path=artifact, window_label="w")


def test_an_artifact_from_a_different_split_is_refused(tmp_path):
    """Two windows scored on different held-out sets are not a trend."""
    artifact = _mutated(tmp_path, ACI, split_digest="f" * 64)

    with pytest.raises(SplitDriftError):
        prepare_artifact(agent_name="note_structuring",
                         artifact_path=artifact, window_label="w")


# ---------- round trips against a real database ----------

def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(),
                              reason="no reachable Postgres")

WINDOW = "p3-1-test-window"


@pytest.fixture
def clean_window():
    yield WINDOW
    with get_conn() as conn:
        conn.execute("DELETE FROM eval_runs WHERE window_label = %s", (WINDOW,))


def _row(row_id: int) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT agent_name, model, model_effort, window_label, dataset_ref, "
            "       n_examples, accuracy, f1, precision, recall, metrics, "
            "       created_at FROM eval_runs WHERE id = %s", (row_id,))
        return dict(zip([c.name for c in cur.description], cur.fetchone()))


@needs_db
def test_the_aci_window_round_trips_with_the_july_timestamp(clean_window):
    row_id = score_artifact(agent_name="note_structuring", artifact_path=ACI,
                            window_label=clean_window)

    row = _row(row_id)
    assert row["agent_name"] == "note_structuring"
    assert row["model_effort"] == "high"
    assert row["n_examples"] == 120
    # REAL is single precision, so the stored f1 is the float32 image of the
    # published number rather than the number itself. Comparing exactly here
    # would test the column's width, not the metric.
    assert row["f1"] == pytest.approx(ACI_F1, rel=1e-6)
    assert row["accuracy"] is not None
    assert row["created_at"] == ACI_MEASURED_AT
    # The JSONB carries what the columns cannot: the metrics with no column of
    # their own, and the provenance.
    assert row["metrics"]["hallucination_rate"] == pytest.approx(0.0289562, rel=1e-4)
    assert row["metrics"]["provenance"]["generation"]["max_tokens"] is None


@needs_db
def test_the_primock_window_stores_a_null_accuracy_beside_real_metrics(
        clean_window):
    row_id = score_artifact(agent_name="note_structuring",
                            artifact_path=PRIMOCK, window_label=clean_window)

    row = _row(row_id)
    assert row["accuracy"] is None, (
        "placement is not scorable against an unsectioned note, so the column "
        "must be NULL rather than carrying the 1.0 the arithmetic produces")
    assert row["f1"] is not None
    assert row["precision"] is not None
    assert row["recall"] is not None


@needs_db
def test_a_refused_write_inserts_nothing_at_all(clean_window):
    """The guard runs before the connection is opened, so a refusal is not a
    rolled-back insert; it is no insert. Counted, because "raises" alone would
    still pass if a row had already landed."""
    def count() -> int:
        with get_conn() as conn:
            return conn.execute(
                "SELECT count(*) FROM eval_runs WHERE window_label = %s",
                (clean_window,)).fetchone()[0]

    before = count()
    with pytest.raises(UnscoreableAgentError):
        record_eval_run(
            agent_name="coding", model="claude-opus-4-8", model_effort="high",
            window_label=clean_window, dataset_ref="aci-bench-heldout-v1",
            n_examples=113, metrics={"f1": 0.97}, provenance={},
            measured_at=datetime.now(timezone.utc))

    assert count() == before
