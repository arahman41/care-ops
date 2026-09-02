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
