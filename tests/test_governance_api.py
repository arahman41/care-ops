"""P3-5: governance/api.py's read queries against a real database.

Guarded by needs_db and self-contained (own fixtures, own cleanup),
mirroring tests/test_registry.py and tests/test_transparency.py: CI's
Postgres has only db/schema.sql applied, nothing seeded, so no test here may
assume any row already exists, and every fixture cleans up what it inserted.
"""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from governance.api import accuracy_trend, inventory_rows
from governance.evaluate import record_eval_run
from shared.config import settings
from shared.db import get_conn


def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="no reachable Postgres")


@pytest.fixture
def inventory_row():
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO model_inventory (agent_name, model, version, "
            "intended_use) VALUES (%s, %s, %s, %s) RETURNING id",
            ("p3-5-test-agent", "test-model", "v1", "test row for P3-5"),
        ).fetchone()
    yield row[0]
    with get_conn() as conn:
        conn.execute("DELETE FROM model_inventory WHERE agent_name = %s",
                     ("p3-5-test-agent",))


@pytest.fixture
def trend_windows():
    """Two note_structuring windows, dated so the ordering assertion is
    unambiguous, filed through the real guarded writer rather than a raw
    INSERT so this also exercises record_eval_run's own contract."""
    labels = ("p3-5-test-w1", "p3-5-test-w2")
    record_eval_run(
        agent_name="note_structuring", model="claude-sonnet-5",
        model_effort="high", window_label=labels[0],
        dataset_ref="aci-bench-heldout-v1", n_examples=5,
        metrics={"accuracy": 0.5, "f1": 0.5, "precision": 0.5, "recall": 0.5},
        provenance={}, measured_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    record_eval_run(
        agent_name="note_structuring", model="claude-sonnet-5",
        model_effort="high", window_label=labels[1],
        dataset_ref="aci-bench-heldout-v1", n_examples=5,
        metrics={"accuracy": 0.6, "f1": 0.6, "precision": 0.6, "recall": 0.6},
        provenance={}, measured_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    yield labels
    with get_conn() as conn:
        conn.execute("DELETE FROM eval_runs WHERE window_label = ANY(%s)",
                     (list(labels),))


# ---------- inventory_rows ----------

@needs_db
def test_inventory_rows_returns_the_seeded_row(inventory_row):
    rows = inventory_rows()
    match = [r for r in rows if r["agent_name"] == "p3-5-test-agent"]
    assert len(match) == 1
    assert match[0]["intended_use"] == "test row for P3-5"
    assert match[0]["id"] == inventory_row


@needs_db
def test_inventory_rows_is_a_plain_dump_not_a_report_mapping(inventory_row):
    """Distinct from transparency.build_report(): raw column names, no HTI-1
    category keys, so a caller gets the row shape, not the report shape."""
    row = next(r for r in inventory_rows()
              if r["agent_name"] == "p3-5-test-agent")
    assert "version" in row
    assert "Purpose of the intervention" not in row


# ---------- accuracy_trend ----------

@needs_db
def test_accuracy_trend_is_ordered_oldest_first(trend_windows):
    rows = accuracy_trend(agent_name="note_structuring")
    labels_in_order = [r["window_label"] for r in rows
                       if r["window_label"] in trend_windows]
    assert labels_in_order == list(trend_windows)


@needs_db
def test_accuracy_trend_filter_excludes_other_agents(trend_windows):
    rows = accuracy_trend(agent_name="coding")
    assert not any(r["window_label"] in trend_windows for r in rows)


@needs_db
def test_accuracy_trend_with_no_filter_still_includes_the_agent(trend_windows):
    rows = accuracy_trend()
    labels = {r["window_label"] for r in rows}
    assert set(trend_windows) <= labels


@needs_db
def test_accuracy_trend_carries_the_real_accuracy_values_not_nulled(
        trend_windows):
    """note_structuring is in SCOREABLE, so these rows must round-trip real
    numbers, not the NULLs an unscoreable agent's rows would carry."""
    rows = accuracy_trend(agent_name="note_structuring")
    row = next(r for r in rows if r["window_label"] == "p3-5-test-w1")
    assert row["f1"] == pytest.approx(0.5)
    assert row["accuracy"] == pytest.approx(0.5)
