"""Seed model_inventory with the 4 clinical agents (P3-4).

    python scripts/seed_model_inventory.py

Idempotent: upserts on (agent_name, model, version), the table's existing
unique constraint, so running this again after a routing change in
shared/llm.py ADDS a new row rather than mutating history, and old rows stay
queryable by whichever window they were current for.

model and version come from shared.llm.ROUTING, never hand-typed, so this
script cannot silently fall out of sync with what actually runs.

transparency and eval_judge are deliberately excluded: infrastructure with no
agent_decisions rows and no decision a clinician sees, not decision support
interventions in the HTI-1 sense.

Every string below traces to language this project has already committed and
vetted. Where a field has nothing truthful to say yet, it is None, with a
comment explaining why, not a plausible-sounding placeholder.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from governance.evaluate import UNSCOREABLE                      # noqa: E402
from services.agent_care_gap.rules import (                      # noqa: E402
    CITATIONS_VERIFIED_ON,
    RULES,
)
from shared.db import get_conn                                   # noqa: E402
from shared.llm import ROUTING                                   # noqa: E402

# P2-4, docs/ROADMAP.md: routed to claude-opus-4-8 at high ON COST, not a
# demonstrated quality win. Paired delta 0.70 points, 95% BCa CI
# [-0.73, 2.22], straddling zero.
_CODING_EXTERNAL_VALIDATION = (
    "Benchmarked against claude-sonnet-5 at xhigh on the ACI-Bench held-out "
    "set (P2-4, 2026-08-07): paired delta in not-found rate 0.70 points, 95% "
    "BCa CI [-0.73, 2.22]. The interval straddles zero, so this is NOT a "
    "demonstrated quality win over the alternative benchmarked; the routing "
    "decision was cost ($3.16 vs $4.01 per 120 notes), not accuracy.")

_STRUCTURING_TRAINING_NOTE = (
    "Scored asymmetrically against a clinician-written reference note: "
    "recall is scored against the note (did the model capture what the "
    "clinician wrote), precision against the transcript (is what the model "
    "wrote actually said). See governance.evaluate.score_structuring.")

_STRUCTURING_CAUTION = (
    "The PriMock57 held-out window (n=7, audio-sourced) is too small to "
    "quote as a headline beside ACI-Bench's n=120, and its accuracy is NULL "
    "by construction: placement is not scorable against an unsectioned GP "
    "note. See docs/ROADMAP.md P3-2.")

_care_gap_sources = "; ".join(
    f"{r.source.organization}, \"{r.source.title}\""
    f"{f' (grade {r.source.grade})' if r.source.grade else ''}, {r.source.year}"
    for r in RULES)

_ROWS = [
    dict(
        agent_name="note_structuring",
        intended_use=(
            "Structures a clinical encounter transcript or dictation into a "
            "four-section SOAP note for clinician review."),
        training_data_note=_STRUCTURING_TRAINING_NOTE,
        known_limitations=UNSCOREABLE.get(
            "note_structuring",
            "Recall and precision are measured against a held-out set; "
            "placement accuracy isolates structuring skill from capture "
            "skill. See governance.evaluate.score_structuring."),
        cautioned_out_of_scope_use=_STRUCTURING_CAUTION,
        fairness_process_note=None,  # no fairness process is defined for a
        # structuring task with no protected-class-conditioned outcome.
        external_validation_note=None,  # answered live: see transparency.py.
        maintenance_schedule=None,  # no re-benchmark cadence is defined yet.
    ),
    dict(
        agent_name="coding",
        intended_use=(
            "Suggests ICD-10-CM and HCPCS Level II billing codes from a SOAP "
            "note, for human review before submission."),
        training_data_note=None,  # hosted model; no project-controlled
        # training data to disclose beyond the vendor's own model card.
        known_limitations=UNSCOREABLE["coding"],
        cautioned_out_of_scope_use=UNSCOREABLE["coding"],
        fairness_process_note=None,
        external_validation_note=_CODING_EXTERNAL_VALIDATION,
        maintenance_schedule=None,
    ),
    dict(
        agent_name="care_gap",
        intended_use=(
            "Flags candidate preventive-care gaps (screening, monitoring) "
            "from keyword matches in a SOAP note, for clinician review."),
        training_data_note=(
            "Deterministic rule matching, not a trained model. Haiku is used "
            "only to phrase the flagged gap in prose; it does not decide "
            "whether a gap fires."),
        known_limitations=UNSCOREABLE["care_gap"],
        cautioned_out_of_scope_use=(
            "A keyword match carries no age, interval, or already-done "
            "check. This matters most for LIPID_SCREENING, whose cited "
            "USPSTF grade B is scoped to adults 40-75 with at least one CVD "
            "risk factor and a calculated 10-year CVD risk of 10% or "
            "greater; a keyword scan cannot evaluate that threshold, so a "
            "fired gap does not mean the graded recommendation applies to "
            "this patient. Every gap is a candidate flag for clinician "
            "review, never a confirmed gap."),
        fairness_process_note=(
            f"Every rule traces to a graded published guideline, verified "
            f"{CITATIONS_VERIFIED_ON}: {_care_gap_sources}."),
        external_validation_note=None,
        maintenance_schedule=(
            f"Citations are point-in-time, verified {CITATIONS_VERIFIED_ON}, "
            f"against guidelines that are revised on their own schedule. No "
            f"automated re-verification exists yet."),
    ),
    dict(
        agent_name="prior_auth",
        intended_use=(
            "Flags SOAP note items that may require prior authorization, for "
            "clinician review."),
        training_data_note=None,
        known_limitations=UNSCOREABLE["prior_auth"],
        cautioned_out_of_scope_use=UNSCOREABLE["prior_auth"],
        fairness_process_note=None,
        external_validation_note=None,
        maintenance_schedule=None,
    ),
]


def seed() -> None:
    with get_conn() as conn:
        for row in _ROWS:
            model, effort = ROUTING[
                "structuring" if row["agent_name"] == "note_structuring"
                else row["agent_name"]]
            conn.execute(
                "INSERT INTO model_inventory "
                "(agent_name, model, version, intended_use, "
                " training_data_note, known_limitations, "
                " cautioned_out_of_scope_use, fairness_process_note, "
                " external_validation_note, maintenance_schedule) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (agent_name, model, version) DO UPDATE SET "
                "  intended_use = EXCLUDED.intended_use, "
                "  training_data_note = EXCLUDED.training_data_note, "
                "  known_limitations = EXCLUDED.known_limitations, "
                "  cautioned_out_of_scope_use = "
                "    EXCLUDED.cautioned_out_of_scope_use, "
                "  fairness_process_note = EXCLUDED.fairness_process_note, "
                "  external_validation_note = "
                "    EXCLUDED.external_validation_note, "
                "  maintenance_schedule = EXCLUDED.maintenance_schedule, "
                "  updated_at = now()",
                (row["agent_name"], model, model,  # version == model string;
                 # no separate semver exists for a hosted model here, and the
                 # unique constraint needs a distinct value regardless.
                 row["intended_use"], row["training_data_note"],
                 row["known_limitations"], row["cautioned_out_of_scope_use"],
                 row["fairness_process_note"],
                 row["external_validation_note"],
                 row["maintenance_schedule"]))
    print(f"seeded {len(_ROWS)} model_inventory rows")


if __name__ == "__main__":
    seed()
