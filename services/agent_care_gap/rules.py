"""Rules-based care gap detection. Deterministic and auditable for v1.

Each rule is a keyword trigger mapped to a published screening or follow-up
guideline, and carries that guideline as a structured citation. The LLM is
not used for the core match. Embedding-based matching is the P5-3 stretch
goal.

Matching semantics: triggers include prefix stems (diabet, hypertens, smok).
Patterns therefore use a leading \\b and let the stem extend with \\w*, as in
\\b(?:diabet|...)\\w*. Do NOT wrap a stem with a trailing \\b: \\bdiabet\\b
cannot match "diabetes", because there is no word boundary mid-word. That was
a real bug in the pre-P2-2 placeholder rules.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from shared.schemas import CareGapSource

# Date the citations below were last checked against their primary sources.
# The ADA source is revised annually, so re-verify each January.
CITATIONS_VERIFIED_ON = "2026-07-16"

LIMITATIONS = """These rules are keyword triggers, not clinical reasoning.

- No negation handling: "patient denies tobacco use" still fires
  TOBACCO_CESSATION.
- No age, interval, or already-done checks. This matters most for
  LIPID_SCREENING, whose cited USPSTF grade B is scoped to adults 40-75 with
  at least one CVD risk factor and a calculated 10-year CVD risk of 10% or
  greater. A keyword scan cannot evaluate that threshold, so a fired gap does
  not mean the graded recommendation applies to this patient.
- Citations are point-in-time, verified on CITATIONS_VERIFIED_ON against
  guidelines that are revised on their own schedule.

Every gap is a candidate flag for clinician review, never a confirmed gap.
"""


class CareGapRule(NamedTuple):
    rule_id: str
    pattern: str                    # matched against lowercased note text
    gap: str
    source: CareGapSource


RULES: tuple[CareGapRule, ...] = (
    CareGapRule(
        rule_id="A1C_MONITORING",
        pattern=r"\b(?:diabet|a1c|hba1c|blood sugar|hyperglycemia)\w*",
        gap=("Diabetes mentioned. A1c monitoring may be due; ADA suggests at "
             "least twice yearly if at goal, quarterly if therapy changed or "
             "not at goal. Confirm last A1c date."),
        # A1C_MONITORING, implements Standards of Care in Diabetes
        # recommendation 6.2. The publication name contains an em dash; the
        # chapter title does not, so the chapter title is stored verbatim and
        # the DOI pins the edition.
        source=CareGapSource(
            organization="American Diabetes Association",
            title="Glycemic Goals, Hypoglycemia, and Hyperglycemic Crises",
            grade="E",
            year=2026,
            url="https://doi.org/10.2337/dc26-S006",
        ),
    ),
    CareGapRule(
        rule_id="HTN_SCREENING",
        pattern=(r"\b(?:hypertens|high blood pressure|elevated bp"
                 r"|elevated blood pressure)\w*"),
        gap=("Hypertension mentioned. Confirm blood pressure "
             "screening/monitoring is current."),
        source=CareGapSource(
            organization="U.S. Preventive Services Task Force",
            title="Hypertension in Adults: Screening",
            grade="A",
            year=2021,
            url=("https://www.uspreventiveservicestaskforce.org/uspstf/"
                 "recommendation/hypertension-in-adults-screening"),
        ),
    ),
    CareGapRule(
        rule_id="LIPID_SCREENING",
        pattern=(r"\b(?:cholesterol|lipid|statin|hyperlipidemia"
                 r"|dyslipidemia)\w*"),
        gap=("Lipid or cholesterol topic mentioned. Confirm lipid screening "
             "and CVD risk assessment are current. USPSTF recommends a statin "
             "for adults 40-75 who have at least one CVD risk factor and a "
             "calculated 10-year CVD risk of 10% or greater."),
        # LIPID_SCREENING. Grade B is scoped to adults 40-75 with >=1 CVD risk
        # factor AND 10-year CVD risk >=10%. At 7.5% to <10% it is grade C, and
        # at 76+ it is an I statement. The gap text carries the threshold so the
        # grade is not overclaimed; see LIMITATIONS.
        source=CareGapSource(
            organization="U.S. Preventive Services Task Force",
            title=("Statin Use for the Primary Prevention of Cardiovascular Disease "
                   "in Adults: Preventive Medication"),
            grade="B",
            year=2022,
            url=("https://www.uspreventiveservicestaskforce.org/uspstf/"
                 "recommendation/statin-use-in-adults-preventive-medication"),
        ),
    ),
    CareGapRule(
        rule_id="TOBACCO_CESSATION",
        pattern=r"\b(?:smok|tobacco|vaping|nicotine|cigarette)\w*",
        gap=("Tobacco or nicotine use mentioned. Cessation counseling and "
             "pharmacotherapy are recommended for adults who smoke; confirm "
             "and offer support."),
        # TOBACCO_CESSATION. The grade A covers behavioral counselling plus
        # FDA-approved pharmacotherapy for adults who smoke. USPSTF issued an I
        # statement for e-cigarettes as a cessation aid in the same 2021
        # recommendation, so the gap text must not imply grade A applies to a
        # vaping trigger.
        source=CareGapSource(
            organization="U.S. Preventive Services Task Force",
            title=("Tobacco Smoking Cessation in Adults, Including Pregnant "
                   "Persons: Interventions"),
            grade="A",
            year=2021,
            url=("https://www.uspreventiveservicestaskforce.org/uspstf/"
                 "recommendation/tobacco-use-in-adults-and-pregnant-women-"
                 "counseling-and-interventions"),
        ),
    ),
)


def find_gaps(text: str) -> list[dict]:
    hits = []
    lower = text.lower()
    for rule in RULES:
        m = re.search(rule.pattern, lower)
        if m:
            hits.append({"gap": rule.gap, "rule_id": rule.rule_id,
                         "evidence": f"matched '{m.group(0)}'",
                         "source": rule.source.model_dump()})
    return hits
