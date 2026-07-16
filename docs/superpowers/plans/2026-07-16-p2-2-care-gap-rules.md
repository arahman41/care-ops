# P2-2: Care Gap Agent Real Rule Set Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four uncited placeholder rules in `services/agent_care_gap/rules.py` with four rules that each carry a verified guideline citation, fix the latent regex bug that stops the stems from matching real words, and prove both with per-rule fire/no-fire tests.

**Architecture:** `rules.py` stays a declarative data table with no LLM in the match path, but each rule becomes a typed `CareGapRule` record carrying its trigger, gap text, and a `CareGapSource` citation. `shared/schemas.py` gains `CareGapSource` and a required `source` field on `CareGapItem`, so the citation flows to the `agent_decisions` registry row through the existing `output` JSONB column with no string parsing. `app.py` needs one change (a confidence constant); the `CareGapItem(**g)` construction line already handles the nested model.

**Tech Stack:** Python, pydantic, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-15-p2-2-care-gap-rules-design.md`

**Model/effort:** per `docs/MODEL-EFFORT-GUIDE.md`, P2-2 recommends `/model opus` and `/effort xhigh` ("needs citable guidelines, high hallucination risk to verify"). Confirm the session matches before Task 3, which is the clinical-content task.

**Worktree:** create one before Task 1 (see superpowers:using-git-worktrees), branch `p2-2-care-gap-rules`, matching the P2-1 pattern. The spec is already committed on `main`.

---

## Citations are already verified. Do not re-derive them.

The spec's citation verification gate was closed on 2026-07-16 against primary
sources, and the results are recorded in the spec's section 3. **Task 3 must
transcribe that table exactly.** Do not "improve", re-look-up, or re-word a
citation while implementing. Two specific traps, both already hit once:

- A web search summary claims ADA recommendation 6.2 carries grade **B**. The
  primary source says **E**. `E` is correct. Do not change it.
- `diabetesjournals.org` returns 403 to automated fetchers, so a failed fetch
  is not evidence that a citation is wrong.

If you believe a citation is wrong, stop and raise it with the user rather
than editing it. A wrong citation here is worse than no tool.

---

## Chunk 1: Schema, rules, and citations

### Task 1: Add the `CareGapSource` model

Standalone model, nothing references it yet, so the suite stays green.

**Files:**
- Modify: `shared/schemas.py` (insert above `CareGapItem`, currently line 50)
- Test: `tests/test_schemas.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schemas.py`:

```python
def test_care_gap_source_requires_core_citation_fields():
    with pytest.raises(ValidationError):
        CareGapSource(organization="USPSTF", title="x", year=2021)  # no url


def test_care_gap_source_grade_is_optional():
    src = CareGapSource(organization="USPSTF", title="x", year=2021,
                        url="https://example.org")
    assert src.grade is None
```

Update the import at the top of the file to include `CareGapSource`:

```python
from shared.schemas import (SoapNote, PriorAuthOutput, CareGapOutput,
                            CareGapSource, CodeSuggestion)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_schemas.py -q`
Expected: FAIL at import, `ImportError: cannot import name 'CareGapSource'`.

- [ ] **Step 3: Write the minimal implementation**

In `shared/schemas.py`, insert directly above `class CareGapItem`:

```python
class CareGapSource(BaseModel):
    """The published guideline a care gap rule implements.

    Verified against primary sources on the date recorded in
    services/agent_care_gap/rules.py::CITATIONS_VERIFIED_ON.
    """
    organization: str               # e.g. "U.S. Preventive Services Task Force"
    title: str                      # guideline or chapter title, as published
    grade: str | None = None        # USPSTF A/B/C/I, ADA A/B/C/E; None if ungraded
    year: int
    url: str
```

`grade` is nullable because not every citable guideline carries a letter grade,
and a future ungraded source must not be forced to invent one.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_schemas.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add shared/schemas.py tests/test_schemas.py
git commit -m "feat(P2-2): add CareGapSource citation model"
```

---

### Task 2: Restructure the rules table and fix the regex bug

Renames the four rule IDs, converts the table to typed records, and fixes the
matching semantics. No citations yet, so the suite stays green.

**Files:**
- Modify: `services/agent_care_gap/rules.py` (full rewrite, same file)
- Test: `tests/test_care_gap_rules.py` (rewrite)

**Background on the bug being fixed:** the current pattern
`r"\b(diabet|a1c|hba1c|blood sugar)\b"` can never match "diabetes". `\bdiabet\b`
requires a word boundary between "diabet" and "es", and there isn't one. The
existing `test_diabetes_triggers_a1c_rule` passes only by accident, because its
note also contains the full phrase "blood sugar". Every new firing test below
uses the **bare stem word**, so a regression back to a trailing `\b` turns the
suite red.

- [ ] **Step 1: Write the failing tests**

Replace the whole contents of `tests/test_care_gap_rules.py`:

```python
"""P2-2: the rules engine is deterministic, so it is fully unit testable.

Every firing test below uses a bare stem word ("diabetes", "hypertension",
"cholesterol", "smoker") on purpose. The pre-P2-2 patterns wrapped prefix
stems as \\b(diabet|...)\\b, whose trailing \\b prevented the stem from ever
matching the real word. These tests pin that fix.
"""
from services.agent_care_gap.rules import RULES, find_gaps


def _rule_ids(text: str) -> set[str]:
    return {h["rule_id"] for h in find_gaps(text)}


# ---------- A1C_MONITORING ----------

def test_a1c_monitoring_fires_on_bare_diabetes():
    assert "A1C_MONITORING" in _rule_ids("Patient has diabetes.")


def test_a1c_monitoring_does_not_fire_without_diabetes_terms():
    assert "A1C_MONITORING" not in _rule_ids("Patient here for an ankle check.")


# ---------- HTN_SCREENING ----------

def test_htn_screening_fires_on_bare_hypertension():
    assert "HTN_SCREENING" in _rule_ids("Assessment: hypertension.")


def test_htn_screening_does_not_fire_on_a_normal_bp_reading():
    # "blood pressure" alone is deliberately not a trigger: the rule fires on
    # high/elevated BP, so a normal reading must stay quiet.
    assert "HTN_SCREENING" not in _rule_ids("Blood pressure 118/76, normal.")


# ---------- LIPID_SCREENING ----------

def test_lipid_screening_fires_on_bare_cholesterol():
    assert "LIPID_SCREENING" in _rule_ids("History of high cholesterol.")


def test_lipid_screening_fires_on_plural_lipids():
    """LIPID_SCREENING's regression pin.

    Unlike the other rules, this one's spec-named stem word ("high
    cholesterol") is a whole word, so it matches even under the buggy
    trailing-\\b pattern and cannot pin the fix. "lipids" can: \\blipid\\b
    fails on it, \\blipid\\w* succeeds.
    """
    assert "LIPID_SCREENING" in _rule_ids("Recheck lipids in 3 months.")


def test_lipid_screening_does_not_fire_without_lipid_terms():
    assert "LIPID_SCREENING" not in _rule_ids("Patient reports allergies.")


# ---------- TOBACCO_CESSATION ----------

def test_tobacco_cessation_fires_on_bare_smoker():
    assert "TOBACCO_CESSATION" in _rule_ids("Patient is a smoker.")


def test_tobacco_cessation_does_not_fire_without_tobacco_terms():
    assert "TOBACCO_CESSATION" not in _rule_ids("Patient reports allergies.")


# ---------- engine-level ----------

def test_clean_note_has_no_gaps():
    assert find_gaps("Patient here for a routine ankle check.") == []


def test_evidence_records_the_matched_span():
    hits = find_gaps("Patient has diabetes.")
    a1c = next(h for h in hits if h["rule_id"] == "A1C_MONITORING")
    assert "diabetes" in a1c["evidence"]


def test_rule_ids_are_unique():
    ids = [r.rule_id for r in RULES]
    assert len(ids) == len(set(ids))
```

Note the old `test_diabetes_triggers_a1c_rule` is deliberately **deleted**, not
updated: `test_a1c_monitoring_fires_on_bare_diabetes` supersedes it and covers
the same case more strictly. `test_clean_note_has_no_gaps` is kept as-is.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_care_gap_rules.py -q`

Note the import will NOT fail: `RULES` does exist today, as a dict. The expected
failures, and **why each one fails matters** because it tells you whether the red
you are seeing is the red you want:

| Test | Fails because | Current `find_gaps` output |
|---|---|---|
| `test_a1c_monitoring_fires_on_bare_diabetes` | **the regex bug**, `\bdiabet\b` cannot match "diabetes". The rename is incidental. | `[]` |
| `test_htn_screening_fires_on_bare_hypertension` | **the regex bug**, `\bhypertens\b` cannot match "hypertension". | `[]` |
| `test_tobacco_cessation_fires_on_bare_smoker` | **the regex bug**, `\bsmok\b` cannot match "smoker". | `[]` |
| `test_lipid_screening_fires_on_bare_cholesterol` | **the rename only.** "cholesterol" is a whole word, so it matches even under the buggy pattern. This test does not pin the regex fix, which is why the next one exists. | `[{"rule_id": "LIPID_PANEL", ...}]` |
| `test_lipid_screening_fires_on_plural_lipids` | **the regex bug**, `\blipid\b` cannot match "lipids". This is LIPID_SCREENING's regression pin. | `[]` |
| `test_evidence_records_the_matched_span` | `StopIteration`, since `find_gaps` returns `[]` for "Patient has diabetes." | `[]` |
| `test_rule_ids_are_unique` | `AttributeError`, `RULES` is a dict so iterating yields key strings, which have no `.rule_id` | n/a |

Three of the four bare-stem firing tests fail on the regex bug, which is the
point of the task. If any of those three PASSES, the regex fix is not being
tested and the test input is wrong: stop and fix the test, not the code.

- [ ] **Step 2a: Confirm the regex bug is real before fixing it**

Do not take the table above on faith. Reproduce it:

```bash
python -c "
import re
old = r'\b(diabet|a1c|hba1c|blood sugar)\b'
print('old pattern on \'diabetes\':', re.search(old, 'patient has diabetes.'))
new = r'\b(?:diabet|a1c|hba1c|blood sugar|hyperglycemia)\w*'
print('new pattern on \'diabetes\':', re.search(new, 'patient has diabetes.'))
"
```

Expected: `None` for the old pattern (this is the bug), a match object spanning
`diabetes` for the new one.

- [ ] **Step 3: Write the minimal implementation**

Replace the whole contents of `services/agent_care_gap/rules.py`:

```python
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
    source: CareGapSource | None    # Task 3 fills these in and drops the None


RULES: tuple[CareGapRule, ...] = (
    CareGapRule(
        rule_id="A1C_MONITORING",
        pattern=r"\b(?:diabet|a1c|hba1c|blood sugar|hyperglycemia)\w*",
        gap=("Diabetes mentioned. A1c monitoring may be due; ADA suggests at "
             "least twice yearly if at goal, quarterly if therapy changed or "
             "not at goal. Confirm last A1c date."),
        source=None,  # added in Task 3
    ),
    CareGapRule(
        rule_id="HTN_SCREENING",
        pattern=(r"\b(?:hypertens|high blood pressure|elevated bp"
                 r"|elevated blood pressure)\w*"),
        gap=("Hypertension mentioned. Confirm blood pressure "
             "screening/monitoring is current."),
        source=None,  # added in Task 3
    ),
    CareGapRule(
        rule_id="LIPID_SCREENING",
        pattern=(r"\b(?:cholesterol|lipid|statin|hyperlipidemia"
                 r"|dyslipidemia)\w*"),
        gap=("Lipid or cholesterol topic mentioned. Confirm lipid screening "
             "and CVD risk assessment are current. USPSTF recommends a statin "
             "for adults 40-75 who have at least one CVD risk factor and a "
             "calculated 10-year CVD risk of 10% or greater."),
        source=None,  # added in Task 3
    ),
    CareGapRule(
        rule_id="TOBACCO_CESSATION",
        pattern=r"\b(?:smok|tobacco|vaping|nicotine|cigarette)\w*",
        gap=("Tobacco or nicotine use mentioned. Cessation counseling and "
             "pharmacotherapy are recommended for adults who smoke; confirm "
             "and offer support."),
        source=None,  # added in Task 3
    ),
)


def find_gaps(text: str) -> list[dict]:
    hits = []
    lower = text.lower()
    for rule in RULES:
        m = re.search(rule.pattern, lower)
        if m:
            hits.append({"gap": rule.gap, "rule_id": rule.rule_id,
                         "evidence": f"matched '{m.group(0)}'"})
    return hits
```

`source=None` is a deliberate two-step: Task 3 fills it in together with the
required `CareGapItem.source` field, so each commit leaves the suite green.
Nothing enforces the `None` away in the meantime (`NamedTuple` does no runtime
validation and `make lint` is ruff only, no mypy), so the guard is
`test_every_rule_has_a_complete_citation` in Task 3, which fails loudly on any
rule still holding `None`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_care_gap_rules.py -q`
Expected: PASS (12 tests).

Then confirm nothing else regressed on the rename:

Run: `pytest -q`
Expected: PASS. If anything outside `tests/test_care_gap_rules.py` fails, it is
referencing an old rule ID (`A1C_OVERDUE`, `BP_FOLLOWUP`, `LIPID_PANEL`,
`SMOKING_COUNSEL`); fix the reference, do not revert the rename.

- [ ] **Step 5: Commit**

```bash
git add services/agent_care_gap/rules.py tests/test_care_gap_rules.py
git commit -m "fix(P2-2): rename care gap rules and fix prefix-stem regex bug"
```

---

### Task 3: Attach the verified citations

**This is the clinical-content task.** Transcribe the spec's verified table
exactly. See the "Citations are already verified" section above.

**Files:**
- Modify: `shared/schemas.py` (`CareGapItem`)
- Modify: `services/agent_care_gap/rules.py` (the four `source=None` slots)
- Test: `tests/test_care_gap_rules.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_care_gap_rules.py`, and extend the import line to
`from services.agent_care_gap.rules import CITATIONS_VERIFIED_ON, RULES, find_gaps`:

```python
# ---------- citations ----------

def test_every_rule_has_a_complete_citation():
    """The P2-2 exit criterion, enforced in code: no rule may be uncited.

    This is what stops a future rule being added without a guideline.
    """
    for rule in RULES:
        src = rule.source
        assert src is not None, f"{rule.rule_id} has no source"
        assert src.organization, f"{rule.rule_id} has no organization"
        assert src.title, f"{rule.rule_id} has no title"
        assert src.url.startswith("https://"), f"{rule.rule_id} url: {src.url}"
        assert 2000 <= src.year <= 2100, f"{rule.rule_id} year: {src.year}"


def test_fired_gaps_carry_their_citation():
    hits = find_gaps("Patient has diabetes.")
    a1c = next(h for h in hits if h["rule_id"] == "A1C_MONITORING")
    assert a1c["source"]["organization"] == "American Diabetes Association"
    assert a1c["source"]["grade"] == "E"


def test_care_gap_item_round_trips_with_nested_source():
    """The registry logs output.model_dump(), so the nested model must survive."""
    from shared.schemas import CareGapItem

    hits = find_gaps("Patient is a smoker.")
    item = CareGapItem(**next(h for h in hits
                              if h["rule_id"] == "TOBACCO_CESSATION"))
    dumped = item.model_dump()
    assert CareGapItem(**dumped) == item
    assert dumped["source"]["grade"] == "A"


def test_citations_verified_date_is_recorded():
    assert CITATIONS_VERIFIED_ON == "2026-07-16"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_care_gap_rules.py -q`
Expected: FAIL. `test_every_rule_has_a_complete_citation` fails on
`A1C_MONITORING has no source`; `test_fired_gaps_carry_their_citation` fails
with `KeyError: 'source'`.

- [ ] **Step 3: Write the implementation**

First, in `services/agent_care_gap/rules.py`, tighten the `CareGapRule`
annotation now that no rule holds `None`:

```python
    source: CareGapSource
```

Then, in `shared/schemas.py`, add the required field to `CareGapItem`:

```python
class CareGapItem(BaseModel):
    gap: str                        # e.g. overdue A1c screening
    rule_id: str                    # which rule fired
    evidence: str                   # text span or reason
    source: CareGapSource           # the guideline this rule implements
```

Then in `services/agent_care_gap/rules.py`, replace each `source=None` with the
verified citation. **Transcribe exactly:**

```python
# A1C_MONITORING, implements Standards of Care in Diabetes recommendation 6.2.
# The publication name contains an em dash; the chapter title does not, so the
# chapter title is stored verbatim and the DOI pins the edition.
source=CareGapSource(
    organization="American Diabetes Association",
    title="Glycemic Goals, Hypoglycemia, and Hyperglycemic Crises",
    grade="E",
    year=2026,
    url="https://doi.org/10.2337/dc26-S006",
),
```

```python
# HTN_SCREENING
source=CareGapSource(
    organization="U.S. Preventive Services Task Force",
    title="Hypertension in Adults: Screening",
    grade="A",
    year=2021,
    url=("https://www.uspreventiveservicestaskforce.org/uspstf/"
         "recommendation/hypertension-in-adults-screening"),
),
```

```python
# LIPID_SCREENING. Grade B is scoped to adults 40-75 with >=1 CVD risk factor
# AND 10-year CVD risk >=10%. At 7.5% to <10% it is grade C, and at 76+ it is
# an I statement. The gap text carries the threshold so the grade is not
# overclaimed; see LIMITATIONS.
source=CareGapSource(
    organization="U.S. Preventive Services Task Force",
    title=("Statin Use for the Primary Prevention of Cardiovascular Disease "
           "in Adults: Preventive Medication"),
    grade="B",
    year=2022,
    url=("https://www.uspreventiveservicestaskforce.org/uspstf/"
         "recommendation/statin-use-in-adults-preventive-medication"),
),
```

```python
# TOBACCO_CESSATION. The grade A covers behavioral counselling plus
# FDA-approved pharmacotherapy for adults who smoke. USPSTF issued an I
# statement for e-cigarettes as a cessation aid in the same 2021
# recommendation, so the gap text must not imply grade A applies to a vaping
# trigger.
source=CareGapSource(
    organization="U.S. Preventive Services Task Force",
    title=("Tobacco Smoking Cessation in Adults, Including Pregnant Persons: "
           "Interventions"),
    grade="A",
    year=2021,
    url=("https://www.uspreventiveservicestaskforce.org/uspstf/"
         "recommendation/tobacco-use-in-adults-and-pregnant-women-"
         "counseling-and-interventions"),
),
```

Finally, have `find_gaps` emit the citation. Change the `hits.append` call to:

```python
            hits.append({"gap": rule.gap, "rule_id": rule.rule_id,
                         "evidence": f"matched '{m.group(0)}'",
                         "source": rule.source.model_dump()})
```

`model_dump()` (rather than the model instance) keeps `find_gaps` returning
plain dicts throughout. `app.py`'s existing `CareGapItem(**g)` line reconstructs
the nested `CareGapSource` from the dict, so `app.py` needs no change here.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_care_gap_rules.py -q`
Expected: PASS (16 tests).

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 5: Verify the URLs actually resolve**

Not a unit test (tests must not hit the network). Open each of the four `url`
values in a browser once and confirm each lands on the cited guideline, and that
the grade and year on the page match the record. The ADA DOI must resolve to
"6. Glycemic Goals, Hypoglycemia, and Hyperglycemic Crises: Standards of Care in
Diabetes-2026". Note that `diabetesjournals.org` blocks automated fetchers, so
use a real browser for that one.

**If a page disagrees with the record, STOP and raise it with the user. Do not
edit the citation.** This is the one step in the task that can reopen a closed
verification gate, and the two traps named at the top of this plan both look
exactly like "the page disagrees" at first glance. Expect these two in
particular, neither of which is a mismatch:

- The `TOBACCO_CESSATION` URL slug says
  `tobacco-use-in-adults-and-pregnant-women-counseling-and-interventions`, the
  2015 title. USPSTF keeps legacy slugs through retitles. The page it loads is
  the current 2021 grade A recommendation. Correct as recorded.
- The USPSTF statin page shows grades B, C, and I. `B` is the one this rule
  cites, for adults 40-75 with a risk factor and 10-year risk >=10%. The others
  are different populations, not corrections.

- [ ] **Step 6: Commit**

```bash
git add shared/schemas.py services/agent_care_gap/rules.py tests/test_care_gap_rules.py
git commit -m "feat(P2-2): attach verified guideline citations to care gap rules"
```

---

## Chunk 2: Confidence, docs, and verification

### Task 4: Replace the overclaiming confidence value

`app.py` currently sets `confidence = 0.9 if gaps else 1.0`. The `1.0` claims
certainty that a note contains no care gaps, which a keyword scan cannot know.

**Files:**
- Modify: `services/agent_care_gap/app.py:24-25`
- Test: `tests/test_care_gap_app.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_care_gap_app.py`. This mirrors the structure of
`tests/test_prior_auth_app.py` (a `client` fixture, `monkeypatch.setattr`, a
`/health` test) rather than inventing a second endpoint-test style. Note this is
the first test in the repo to stub `log_decision`: the prior-auth app never calls
it directly, so there is no existing example to copy. The patch target is
`services.agent_care_gap.app.log_decision`, because `app.py` does
`from shared.registry import log_decision`, which binds the name into the app
module's namespace. Stubbing it is what keeps this test off the database.

```python
"""P2-2: the care gap endpoint's wiring. The rules engine's own correctness
is covered by tests/test_care_gap_rules.py. This file's job is the endpoint:
is the confidence honest, and does a rule's citation survive the trip out?

log_decision is stubbed so these stay unit tests. The real registry write is
exercised by hand in the plan's live-verification task.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.agent_care_gap.app import RULE_MATCH_CONFIDENCE, app

SOAP = {"subjective": "s", "objective": "o", "assessment": "a", "plan": "p"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_registry_writes(monkeypatch):
    monkeypatch.setattr("services.agent_care_gap.app.log_decision",
                        lambda **kwargs: None)


def _post(client, subjective: str):
    resp = client.post("/run", json={
        "encounter_id": 1, "note_id": 1,
        "soap": {**SOAP, "subjective": subjective},
    })
    assert resp.status_code == 200
    return resp.json()


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "agent_care_gap"}


def test_a_clean_note_does_not_claim_certainty(client):
    """A keyword scan cannot be certain a note has no gaps, so the no-gap
    case must not report 1.0."""
    out = _post(client, "Patient here for a routine ankle check.")
    assert out["gaps"] == []
    assert out["confidence"] == RULE_MATCH_CONFIDENCE
    assert out["confidence"] < 1.0


def test_a_fired_rule_reports_the_same_fixed_confidence(client):
    out = _post(client, "Patient has diabetes.")
    assert out["gaps"]
    assert out["confidence"] == RULE_MATCH_CONFIDENCE


def test_a_fired_gap_carries_its_citation_through_the_endpoint(client):
    out = _post(client, "Patient has diabetes.")
    gap = next(g for g in out["gaps"] if g["rule_id"] == "A1C_MONITORING")
    assert gap["source"]["url"] == "https://doi.org/10.2337/dc26-S006"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_care_gap_app.py -q`
Expected: FAIL at import, `ImportError: cannot import name
'RULE_MATCH_CONFIDENCE'`.

- [ ] **Step 3: Write the minimal implementation**

In `services/agent_care_gap/app.py`, add below the `app = FastAPI(...)` line:

```python
# A fired rule is a deterministic keyword match, not a calibrated probability.
# The same value is used whether or not a rule fired: a keyword scan cannot be
# certain a note contains no care gaps, so a 1.0 for the empty case would
# overclaim.
RULE_MATCH_CONFIDENCE = 0.9
```

Then replace lines 24-25:

```python
    # Rules are deterministic, so confidence is fixed high when a rule fires.
    confidence = 0.9 if gaps else 1.0
```

with:

```python
    confidence = RULE_MATCH_CONFIDENCE
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_care_gap_app.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add services/agent_care_gap/app.py tests/test_care_gap_app.py
git commit -m "fix(P2-2): care gap confidence no longer claims certainty on clean notes"
```

---

### Task 5: Sync the published contract in the tech design

`docs/TECH-DESIGN.md` section 3.3 documents the Care Gap item shape without
`source`, which the schema change makes stale. This is a transparency-focused
project; a documented shape that no longer matches reality is exactly the drift
the project rules exist to prevent.

**Files:**
- Modify: `docs/TECH-DESIGN.md:98-103`

- [ ] **Step 1: Make the edit**

Replace the Care Gap block:

````markdown
Care Gap (`CareGapOutput`):
```json
{ "agent_name": "care_gap",
  "gaps": [{"gap": "", "rule_id": "", "evidence": ""}],
  "confidence": 0.0 }
```
````

with:

````markdown
Care Gap (`CareGapOutput`):
```json
{ "agent_name": "care_gap",
  "gaps": [{"gap": "", "rule_id": "", "evidence": "",
            "source": {"organization": "", "title": "", "grade": "A",
                       "year": 2021, "url": ""}}],
  "confidence": 0.0 }
```

Every care gap carries the guideline it implements (`source`), so the citation
reaches the registry and the Phase 3 transparency report without string
parsing. `grade` is nullable for ungraded sources.
````

- [ ] **Step 2: Verify the doc matches the code**

Run: `grep -n "source" docs/TECH-DESIGN.md`
Expected: two hits, the new block plus a pre-existing line 137 ("source type and
external ref") that is unrelated. The second hit is not a failure.

Cross-check the new block's field names against `shared/schemas.py::CareGapSource`
by eye; they must match exactly, since the whole point of this task is that the
published contract and the code agree.

- [ ] **Step 3: Commit**

```bash
git add docs/TECH-DESIGN.md
git commit -m "docs(P2-2): document the care gap source field in section 3.3"
```

---

### Task 6: Full regression gate

- [ ] **Step 1: Run the full suite**

Run: `make test`
Expected: PASS, no failures. Record the exact count for the phase gate evidence.

- [ ] **Step 2: Run the linter**

Run: `make lint`
Expected: `ruff check .` clean, no findings.

If ruff flags the unused `CareGapSource` import from Task 2, it should already
be used by Task 3; if not, something in Task 3 was skipped.

- [ ] **Step 3: Confirm no em dashes entered the new text**

The gap strings, `LIMITATIONS`, and the citation titles are generated text under
the project's no-em-dash rule.

Run:
```bash
grep -n $'[—–]' services/agent_care_gap/rules.py services/agent_care_gap/app.py \
  shared/schemas.py docs/TECH-DESIGN.md \
  tests/test_care_gap_rules.py tests/test_care_gap_app.py tests/test_schemas.py
```
Expected: no output. Every one of these files gained generated text in this
task, including `app.py`'s new confidence comment and both test files.

The ADA publication name legitimately contains an em dash, which is exactly why
only the chapter title is stored; if this grep hits `rules.py`, the wrong string
was transcribed and the citation is now a misquote.

---

### Task 7: Live verification

The unit tests stub the registry, so this is the only place the real
`log_decision` write runs. That write is part of the evidence, not a side
effect: Step 5's query can only find a row because Step 4's request actually
persisted one.

**Do not hardcode `encounter_id: 1, note_id: 1`.** `agent_decisions` declares
`NOT NULL REFERENCES encounters(id)` and `NOT NULL REFERENCES notes(id)` (see
`db/schema.sql`), and `make db-init` seeds neither table. Since
`services/agent_care_gap/app.py` calls `log_decision` *before* `return out`, a
hardcoded ID does not produce a nice error, it produces an unhandled 500 in
place of the response you came to inspect. P2-1 hit this exact constraint; mint
real rows with `insert_encounter`/`insert_note` from `shared/db.py`, the same
helpers `services/intake/app.py` uses.

- [ ] **Step 1: Start Postgres**

Docker needs a manual start on this machine.

```bash
docker compose up -d db && make db-init
```

Expected: the `db` container is up and the schema loads without error.

- [ ] **Step 2: Start the service in the background**

On Windows, activate the venv inside the same command or it will not take (this
trapped two subagents during P2-1). `uvicorn` blocks, so background it or use a
second shell.

```bash
source .venv/Scripts/activate && uvicorn services.agent_care_gap.app:app --port 8002 &
```

Port 8002 is free: `docker-compose.yml` publishes only 8000 (intake), 8001
(orchestrator), and 5433 (db).

- [ ] **Step 3: Seed a real encounter and note**

```bash
source .venv/Scripts/activate && python - <<'EOF'
from shared.db import insert_encounter, insert_note
from shared.schemas import SoapNote

soap = SoapNote(
    subjective="Patient is a smoker with diabetes and high cholesterol.",
    objective="BP 152/94. Recheck lipids in 3 months.",
    assessment="Hypertension, type 2 diabetes, hyperlipidemia.",
    plan="Follow up in 3 months.",
)
encounter_id = insert_encounter(None, "manual-verification")
note_id = insert_note(encounter_id, soap.model_dump(),
                      "manual-verification", "high")
print(f"encounter_id={encounter_id} note_id={note_id}")
print(soap.model_dump_json())
EOF
```

Expected: real integer IDs printed. Use them in Step 4.

- [ ] **Step 4: Post the note, using the IDs from Step 3**

Substitute the real IDs for `<ENCOUNTER_ID>` and `<NOTE_ID>`:

```bash
curl -s -X POST http://localhost:8002/run -H 'Content-Type: application/json' -d '{
  "encounter_id": <ENCOUNTER_ID>, "note_id": <NOTE_ID>,
  "soap": {"subjective": "Patient is a smoker with diabetes and high cholesterol.",
           "objective": "BP 152/94. Recheck lipids in 3 months.",
           "assessment": "Hypertension, type 2 diabetes, hyperlipidemia.",
           "plan": "Follow up in 3 months."}
}' | python -m json.tool
```

Expected: HTTP 200, four gaps fire (`A1C_MONITORING`, `HTN_SCREENING`,
`LIPID_SCREENING`, `TOBACCO_CESSATION`), each with a populated `source`, and
`confidence` 0.9. A 500 here means the FK problem above was not avoided.

- [ ] **Step 5: Eyeball each rendered citation**

Read the four `source` blocks in the response. Confirm each organization,
title, grade, year, and url matches the spec's verified table in section 3.
This is the last human check before the citations ship. The same
stop-do-not-edit rule from Task 3 Step 5 applies.

- [ ] **Step 6: Confirm the registry row carries the citation**

Step 4 succeeding means `log_decision` wrote a real row. Confirm the nested
source survived into the `output` JSONB column:

```sql
SELECT output -> 'gaps' -> 0 -> 'source'
FROM agent_decisions WHERE agent_name = 'care_gap'
ORDER BY id DESC LIMIT 1;
```

Expected: the full citation object, not null. This is what the Phase 3
transparency report will read, and it is the reason `source` is a structured
model rather than a string.

There is no local `psql` on this machine; run the query through the db
container:

```bash
docker compose exec db psql -U postgres -d careops -c "SELECT output -> 'gaps' -> 0 -> 'source' FROM agent_decisions WHERE agent_name = 'care_gap' ORDER BY id DESC LIMIT 1;"
```

Check the database name against `.env.example` if that connection is refused.

---

## Definition of done (P2-2 exit gate)

From `docs/ROADMAP.md`: "each rule maps to a documented guideline source and the
rules engine has unit tests for every rule firing and not firing."

- [ ] All four rules carry a `CareGapSource` verified against a primary source
- [ ] `test_every_rule_has_a_complete_citation` enforces that in code, so no
      future rule can be added uncited
- [ ] At least one fire and one no-fire test per rule (nine total: five firing,
      four not), with every rule's regex fix pinned by a firing test whose input
      requires the stem to extend. `LIPID_SCREENING` needs two firing tests to
      get that pin, because "cholesterol" is a whole word and matches even under
      the buggy pattern.
- [ ] `make test` green, `make lint` clean, counts recorded
- [ ] Live run shows the citations rendered in the response and persisted to
      `agent_decisions.output`
- [ ] `docs/TECH-DESIGN.md` section 3.3 matches `shared/schemas.py`

State the gate, show the evidence, and get explicit user confirmation before
starting P2-3 (per CLAUDE.md's phase gates rule).
