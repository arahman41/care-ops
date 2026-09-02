"""P3-4: the transparency report, joined live against real eval_runs data.

Guarded by needs_db, mirroring tests/test_registry.py: local dev has no
standing Postgres unless it was started for this session; CI's postgres:16
service always does.

Assumes scripts/seed_model_inventory.py has already been run against this
database. This module reads what that script wrote; it does not re-seed, so a
test failure here after a ROUTING change means re-run the seed script, not a
bug in this test.
"""
from __future__ import annotations

import psycopg
import pytest

from governance.transparency import HTI1_CATEGORIES, build_report
from shared.config import settings


def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="no reachable Postgres")


@needs_db
def test_report_has_one_row_per_seeded_agent():
    rows = build_report()
    names = {r["agent_name"] for r in rows}
    assert names == {"note_structuring", "coding", "care_gap", "prior_auth"}


@needs_db
def test_every_row_carries_all_nine_categories():
    """Every category is present, even where the honest value is None."""
    for row in build_report():
        missing = [c for c in HTI1_CATEGORIES if c not in row]
        assert not missing, f"{row['agent_name']} is missing {missing}"


@needs_db
def test_an_agent_with_no_eval_runs_reports_not_yet_measured():
    """prior_auth has no held-out set at all, so no eval_runs row exists."""
    row = next(r for r in build_report() if r["agent_name"] == "prior_auth")
    assert row["Quantitative measures of performance"] == "not yet measured"


@needs_db
def test_codings_performance_comes_from_its_own_arms_metrics_jsonb():
    """coding has 2 eval_runs rows (P2-4's two arms), but only ONE of them
    has model='claude-opus-4-8', the routed arm model_inventory records.
    Filtering by model, not just by recency, is what keeps the OTHER arm
    (claude-sonnet-5) out of this report row.
    """
    row = next(r for r in build_report() if r["agent_name"] == "coding")
    perf = row["Quantitative measures of performance"]
    assert "verified_rate" in perf
    assert "sonnet" not in perf.lower()


@needs_db
def test_codings_cautioned_use_is_the_actual_unscoreable_string():
    """Not a paraphrase: the same string governance.evaluate already asserts.

    If someone edits UNSCOREABLE["coding"] without touching this report, this
    test catches the drift, which is the whole point of sourcing from it
    rather than retyping it.
    """
    from governance.evaluate import UNSCOREABLE

    row = next(r for r in build_report() if r["agent_name"] == "coding")
    assert row["Cautioned out-of-scope use of the intervention"] == (
        UNSCOREABLE["coding"])


@needs_db
def test_note_structurings_two_aci_windows_produce_the_p3_3_verdict():
    """Windows 7 and 25. Same verdict scripts/run_drift_check.py reports for
    the same two artifacts: NOT_ATTRIBUTABLE, naming max_tokens.
    """
    row = next(r for r in build_report() if r["agent_name"] == "note_structuring")
    validation = row[
        "Updates and continued validation or fairness assessment schedule"]
    assert "NOT_ATTRIBUTABLE" in validation
    assert "max_tokens" in validation


@needs_db
def test_the_report_never_crashes_on_a_null_max_tokens_side():
    """Smoke test: build_report() runs to completion against the real,
    currently-committed data, top to bottom, for every agent."""
    rows = build_report()
    assert len(rows) == 4
    for row in rows:
        for category in HTI1_CATEGORIES:
            assert category in row
