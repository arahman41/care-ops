"""P3-4: the transparency report, joined live against real eval_runs data.

Guarded by needs_db, mirroring tests/test_registry.py: local dev has no
standing Postgres unless it was started for this session; CI's postgres:16
service always does.

Self-contained, mirroring tests/test_eval_windows.py's clean_windows pattern:
CI's database has an empty model_inventory and an empty eval_runs (only
db/schema.sql is applied, nothing is seeded), while a developer's local
database may hold the real committed history (windows 7, 8, 25, 3, 4). A test
that asserted on that real history would pass locally and fail in CI, which
is exactly what the first version of this file did. Every test here seeds
what it needs (model_inventory via the real seed script, which is a safe
upsert; eval_runs rows via record_eval_run, cleaned up by window_label) and
asserts nothing that depends on data this file did not itself create.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from governance.evaluate import UNSCOREABLE, record_eval_run
from governance.transparency import HTI1_CATEGORIES, build_report
from scripts.seed_model_inventory import seed as seed_model_inventory
from shared.config import settings
from shared.db import get_conn

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "governance" / "eval_artifacts"
JUL = ARTIFACT_DIR / "structuring_aci-bench-heldout-v1_20260714T032403Z.json"
AUG = ARTIFACT_DIR / "structuring_aci-bench-heldout-v1_20260831T205449Z.json"

needs_windows = pytest.mark.skipif(
    not (JUL.exists() and AUG.exists()),
    reason="both committed ACI windows are required")


def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="no reachable Postgres")


@pytest.fixture
def seeded_inventory():
    """model_inventory rows for the 4 clinical agents.

    Calling the real seed script rather than duplicating its content: an
    upsert on (agent_name, model, version) is safe to run against any
    database, local or CI, and this is exactly what a developer would run
    before using the report for real.
    """
    seed_model_inventory()


def _file_window(*, agent_name: str, model: str, window_label: str,
                 dataset_ref: str, n_examples: int, artifact: Path | None,
                 metrics: dict, measured_at: datetime) -> None:
    provenance = {"artifact": artifact.name} if artifact else {}
    record_eval_run(
        agent_name=agent_name, model=model, model_effort="high",
        window_label=window_label, dataset_ref=dataset_ref,
        n_examples=n_examples, metrics=metrics, provenance=provenance,
        measured_at=measured_at)


@pytest.fixture
def clean_transparency_windows():
    labels = ("p3-4-test-w1", "p3-4-test-w2", "p3-4-test-sonnet-arm",
             "p3-4-test-opus-arm")
    yield labels
    with get_conn() as conn:
        conn.execute("DELETE FROM eval_runs WHERE window_label = ANY(%s)",
                     (list(labels),))


# ---------- shape: every category present, whatever eval_runs holds ----------


@needs_db
def test_report_has_one_row_per_seeded_agent(seeded_inventory):
    rows = build_report()
    names = {r["agent_name"] for r in rows}
    assert names == {"note_structuring", "coding", "care_gap", "prior_auth"}


@needs_db
def test_every_row_carries_all_nine_categories(seeded_inventory):
    for row in build_report():
        missing = [c for c in HTI1_CATEGORIES if c not in row]
        assert not missing, f"{row['agent_name']} is missing {missing}"


@needs_db
def test_an_agent_with_no_eval_runs_reports_not_yet_measured(seeded_inventory):
    """prior_auth has no held-out set, so no eval_runs row exists for it in
    ANY environment, real or test: this needs no fixture beyond the seed."""
    row = next(r for r in build_report() if r["agent_name"] == "prior_auth")
    assert row["Quantitative measures of performance"] == "not yet measured"


@needs_db
def test_codings_cautioned_use_is_the_actual_unscoreable_string(seeded_inventory):
    """Not a paraphrase: the same string governance.evaluate already asserts.

    If someone edits UNSCOREABLE["coding"] without touching this report, this
    test catches the drift, which is the whole point of sourcing from it
    rather than retyping it.
    """
    row = next(r for r in build_report() if r["agent_name"] == "coding")
    assert row["Cautioned out-of-scope use of the intervention"] == (
        UNSCOREABLE["coding"])


# ---------- behavior that needs controlled eval_runs data ----------


@needs_db
def test_codings_performance_ignores_the_other_arms_model(
        seeded_inventory, clean_transparency_windows):
    """Two synthetic rows, same benchmark shape as the real P2-4 arms: one
    model='claude-sonnet-5', one model='claude-opus-4-8'. model_inventory's
    coding row has model='claude-opus-4-8' (the routed arm), so the report
    must reflect only the opus row's numbers, never the sonnet arm's.

    Dated AFTER any real committed rows a local dev database might hold, so
    this is the "latest" row regardless of what else is in the table.
    """
    now = datetime.now(timezone.utc)
    metrics_common = {"n_notes": 5, "verified_rate": None,
                      "not_found_rate": None}
    _file_window(agent_name="coding", model="claude-sonnet-5",
                window_label="p3-4-test-sonnet-arm",
                dataset_ref="aci-bench-heldout-v1", n_examples=5,
                artifact=None,
                metrics={**metrics_common, "verified_rate": 11.11},
                measured_at=now)
    _file_window(agent_name="coding", model="claude-opus-4-8",
                window_label="p3-4-test-opus-arm",
                dataset_ref="aci-bench-heldout-v1", n_examples=5,
                artifact=None,
                metrics={**metrics_common, "verified_rate": 99.99},
                measured_at=now + timedelta(seconds=1))

    row = next(r for r in build_report() if r["agent_name"] == "coding")
    perf = row["Quantitative measures of performance"]
    assert "99.99" in perf
    assert "11.11" not in perf


@needs_windows
@needs_db
def test_note_structurings_two_same_dataset_windows_produce_the_p3_3_verdict(
        seeded_inventory, clean_transparency_windows):
    """Two synthetic windows pointing at the two REAL committed artifacts
    P3-3 already characterized. This reproduces P3-3's own verdict
    (NOT_ATTRIBUTABLE, naming max_tokens) without depending on any eval_runs
    row a local database happens to already have, so it holds identically in
    CI's empty database and a developer's populated one.
    """
    base = datetime.now(timezone.utc)
    jul_payload = json.loads(JUL.read_text(encoding="utf-8"))
    aug_payload = json.loads(AUG.read_text(encoding="utf-8"))

    _file_window(agent_name="note_structuring", model="claude-sonnet-5",
                window_label="p3-4-test-w1", dataset_ref="aci-bench-heldout-v1",
                n_examples=jul_payload["n_examples"], artifact=JUL,
                metrics=jul_payload["metrics"], measured_at=base)
    _file_window(agent_name="note_structuring", model="claude-sonnet-5",
                window_label="p3-4-test-w2", dataset_ref="aci-bench-heldout-v1",
                n_examples=aug_payload["n_examples"], artifact=AUG,
                metrics=aug_payload["metrics"],
                measured_at=base + timedelta(seconds=1))

    row = next(r for r in build_report() if r["agent_name"] == "note_structuring")
    validation = row[
        "Updates and continued validation or fairness assessment schedule"]
    assert "NOT_ATTRIBUTABLE" in validation
    assert "max_tokens" in validation


@needs_db
def test_the_report_never_crashes_regardless_of_history(seeded_inventory):
    """Smoke test with no eval_runs fixture at all: the report must run to
    completion whether history exists or not."""
    rows = build_report()
    assert len(rows) == 4
    for row in rows:
        for category in HTI1_CATEGORIES:
            assert category in row
