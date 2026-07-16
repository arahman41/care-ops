# P2-2: Care Gap Agent with a real rule set, Design

## Context

Phase 2 (`docs/ROADMAP.md`) builds three agent services. The Care Gap Agent
(`services/agent_care_gap/`) currently uses four placeholder regex rules with
no citation to any real guideline:

```python
RULES = {
    "A1C_OVERDUE": (r"\b(diabet|a1c|hba1c|blood sugar)\b", "Consider A1c ..."),
    "BP_FOLLOWUP": (r"\b(hypertens|blood pressure|elevated bp)\b", "Blood pressure ..."),
    "LIPID_PANEL": (r"\b(cholesterol|statin|lipid)\b", "Lipid panel ..."),
    "SMOKING_COUNSEL": (r"\b(smok|tobacco|vaping)\b", "Tobacco cessation ..."),
}
```

`tests/test_care_gap_rules.py` has only two tests (diabetes fires A1c, a clean
note fires nothing). The `CareGapItem` schema (`shared/schemas.py`) has fields
`gap`, `rule_id`, `evidence` and no field for a guideline source.

P2-2's exit criteria from the roadmap:

> Replace the four placeholder rules with citable screening and follow-up
> guidelines. Done when each rule maps to a documented guideline source and the
> rules engine has unit tests for every rule firing and not firing.

Per `docs/MODEL-EFFORT-GUIDE.md`, this task is Opus 4.8 at xhigh effort because
it "needs citable guidelines, high hallucination risk to verify." The
correctness risk in this task is not in the code; it is in the accuracy of the
clinical citations. The design treats citation accuracy as a first-class
verification gate, not an afterthought.

## Design

The agent stays a **purely deterministic keyword-rules engine with no LLM call
in the matching path.** The tech design (`docs/TECH-DESIGN.md`) explicitly
values that "the rules engine is fully testable without an LLM," and keeping
the match deterministic confines the one real hallucination risk (the hardcoded
citations) to authoring time rather than runtime. No changes to
`ROUTING`/`shared/llm.py`; the Care Gap Agent does not call a model in v1.

### 1. Schema (`shared/schemas.py`)

Add a structured citation model and attach it to each gap:

```python
class CareGapSource(BaseModel):
    organization: str          # e.g. "U.S. Preventive Services Task Force"
    title: str                 # e.g. "Hypertension in Adults: Screening"
    grade: str | None = None   # USPSTF "A"/"B", ADA "E"; None if ungraded
    year: int
    url: str


class CareGapItem(BaseModel):
    gap: str
    rule_id: str
    evidence: str
    source: CareGapSource      # NEW
```

`grade` is nullable because not every citable guideline carries a letter grade;
USPSTF recommendations do, ADA Standards of Care use an A/B/C/E evidence grade,
and a future non-graded source should not be forced to invent one.

This is the only schema change. It surfaces the citation as a first-class part
of the output, so it flows to the `agent_decisions` registry row (via the
existing `output` JSONB column) and is available to the Phase 3 transparency
report and dashboard without any string parsing.

### 2. Rules engine (`services/agent_care_gap/rules.py`)

Keep `rules.py` a declarative data table, restructured so each rule carries its
citation alongside its trigger and gap text. Concretely, a rule becomes a small
typed record (a dataclass or `NamedTuple`) with fields `rule_id`, `pattern`,
`gap`, and `source` (a `CareGapSource`). `rules.py` imports `CareGapSource` from
`shared/schemas.py` and constructs one per rule. `find_gaps(text)` returns a
list of dicts shaped `{"gap", "rule_id", "evidence", "source"}`. The `source`
value may be returned either as a `CareGapSource` instance or as its
`model_dump()` dict; either works, because the existing `app.py` line
`[CareGapItem(**g) for g in find_gaps(blob)]` lets Pydantic construct (or pass
through) the nested `CareGapSource` in both cases, so `app.py` needs no change
for the schema addition. Returning the dumped dict is the marginally cleaner
choice since `find_gaps` already returns plain dicts for its other fields.

Rejected alternatives: a YAML/JSON rules file (adds a parsing layer and
separates the citation from the code review that must verify it; YAGNI for four
rules) and per-rule matcher classes (over-engineered for keyword rules and
invites the negation-logic scope creep this design explicitly rejects).

**Matching semantics (fixes a latent bug in the placeholder rules).** The
triggers include prefix stems (`diabet`, `hypertens`, `smok`). The current
placeholder pattern wraps them as `\b(diabet|...)\b`, and the trailing `\b`
means the stem never matches the real word: `\bdiabet\b` does not match
"diabetes" because there is no word boundary between "diabet" and "es". The
existing `test_diabetes_triggers_a1c_rule` passes only because its note also
contains "blood sugar". The new rules must use a leading boundary and allow the
stem to extend, e.g. `\b(?:diabet|a1c|hba1c|blood sugar|hyperglycemia)\w*`, so
that a bare "diabetes", "hypertension", or "smoker" fires its rule. To pin this,
at least one firing test per rule uses the bare stem word ("diabetes",
"hypertension", "high cholesterol", "smoker") as its input, so a regression back
to a non-matching pattern turns the suite red.

### 3. The four rules and their citations

Same four clinical domains, each mapped to a real published guideline. Three
are USPSTF; the diabetes rule is ADA because there is no USPSTF management
(A1c monitoring interval) recommendation.

| rule_id | triggers (regex alternation, prefix-permissive; see matching note) | gap framing | source |
|---|---|---|---|
| `A1C_MONITORING` | diabet, a1c, hba1c, blood sugar, hyperglycemia | "Diabetes mentioned. A1c monitoring may be due; ADA suggests at least twice yearly if at goal, quarterly if therapy changed or not at goal. Confirm last A1c date." | American Diabetes Association, "Glycemic Goals, Hypoglycemia, and Hyperglycemic Crises" (Standards of Care in Diabetes section 6, recommendation 6.2), grade E, 2026, https://doi.org/10.2337/dc26-S006 |
| `HTN_SCREENING` | hypertens, high blood pressure, elevated bp, elevated blood pressure | "Hypertension mentioned. Confirm blood pressure screening/monitoring is current." | U.S. Preventive Services Task Force, "Hypertension in Adults: Screening", grade A, 2021 |
| `LIPID_SCREENING` | cholesterol, lipid, statin, hyperlipidemia, dyslipidemia | "Lipid or cholesterol topic mentioned. Confirm lipid screening and CVD risk assessment are current. USPSTF recommends a statin for adults 40-75 who have at least one CVD risk factor and a calculated 10-year CVD risk of 10% or greater." | U.S. Preventive Services Task Force, "Statin Use for the Primary Prevention of Cardiovascular Disease in Adults: Preventive Medication", grade B, 2022 |
| `TOBACCO_CESSATION` | smok, tobacco, vaping, nicotine, cigarette | "Tobacco or nicotine use mentioned. Cessation counseling and pharmacotherapy are recommended for adults who smoke; confirm and offer support." | U.S. Preventive Services Task Force, "Tobacco Smoking Cessation in Adults, Including Pregnant Persons: Interventions", grade A, 2021 |

Three scope and typography notes on this table:

- **The ADA em dash dissolves rather than needs a ruling.** The official
  publication name is "Standards of Care in Diabetes-2026" with a literal em
  dash, which the project no-em-dash rule forbids storing verbatim. But the em
  dash belongs to the *publication* name, not the *chapter* name. The full
  heading is "6. Glycemic Goals, Hypoglycemia, and Hyperglycemic Crises:
  Standards of Care in Diabetes-2026"; the chapter title (before the colon)
  contains no em dash. So `title` holds the chapter title verbatim,
  `organization` holds "American Diabetes Association", `year` holds 2026, and
  `url` holds the edition-pinned DOI. The citation is complete and unambiguous,
  nothing is misquoted, and no em dash is ever stored. No typography divergence
  is required after all.
- `TOBACCO_CESSATION` triggers on `vaping`/`nicotine`, but the cited grade A
  covers behavioral counseling plus FDA-approved pharmacotherapy for smoking;
  USPSTF issued an I statement (insufficient evidence) for e-cigarettes as a
  cessation aid in the same 2021 recommendation. The gap text is therefore
  phrased around "adults who smoke" and must not imply grade-A evidence applies
  to vaping specifically. The trigger stays (a vaping mention is still a
  legitimate candidate flag for human review), but the framing does not
  overclaim the grade.
- `LIPID_SCREENING` carries the same class of overclaim risk, caught at
  verification. The USPSTF grade B applies to adults 40-75 with at least one CVD
  risk factor **and** a calculated 10-year CVD risk of 10% or greater. At a
  10-year risk of 7.5% to under 10% the recommendation drops to grade C
  ("selectively offer"), and at 76 years or older it is an I statement. The gap
  text must therefore carry the risk threshold, not just the risk factor, or it
  attaches a grade B to a broader population than USPSTF graded B.

**Citation verification gate: COMPLETED 2026-07-16.** Every `organization`,
`title`, `grade`, `year`, and `url` above was verified against its primary
source (three USPSTF recommendation pages; the ADA chapter fetched from
diabetesjournals.org). Results:

| rule_id | verdict |
|---|---|
| `HTN_SCREENING` | clean. Title, grade A, 2021 (published April 27, 2021) confirmed verbatim. |
| `TOBACCO_CESSATION` | clean. Title, grade A, 2021 (published January 19, 2021) confirmed. E-cigarette I statement confirmed, so the caution above is well founded. |
| `LIPID_SCREENING` | citation clean (title, grade B, published August 23, 2022); **gap text corrected** to add the 10% risk threshold per the note above. |
| `A1C_MONITORING` | grade E **confirmed correct** verbatim in recommendation 6.2. **Year corrected 2024 to 2026** and **chapter title corrected** (it gained "and Hyperglycemic Crises"). Gap text ("twice yearly if at goal, quarterly if therapy changed or not at goal") confirmed accurate against 6.2 and its narrative. |

Two process notes worth keeping, because they generalize:

1. A web search summary asserted ADA recommendation 6.2 carries grade **B**. The
   primary source says **E**. Trusting the summary would have converted a
   correct citation into an incorrect one. Secondary sources do not close this
   gate; only the primary source does.
2. `diabetesjournals.org` returns 403 to automated fetchers. The chapter was
   readable via browser automation against a real Chrome session. Any future
   re-verification of the ADA citation needs that route.

A wrong citation in a transparency-focused clinical tool is worse than no tool.
Any future edit to the table above re-opens this gate.

**Citation staleness is a standing liability, not a one-time fix.** The ADA
chapter states that the Professional Practice Committee updates the Standards of
Care "annually, or more frequently as warranted." The 2024 citation in the
original draft of this spec was stale within two editions, which is precisely
how it arrived here wrong. Pinning `url` to the edition-specific DOI
(`10.2337/dc26-S006`) means the cited text stays valid permanently, but `year`
will drift from "current guidance" each January. The USPSTF citations are stable
over much longer horizons and are not a near-term concern. Mitigation in scope
for this task, deliberately cheap: a module-level `CITATIONS_VERIFIED_ON` date
constant in `rules.py` (value `2026-07-16`) and a `LIMITATIONS` line noting the
ADA source is revised annually. Tests must not hit the network, so this is a
documented review prompt for a human, not an automated check. Out of scope: any
automated guideline-freshness service.

### 4. Honesty framing

Each fired rule is phrased as a candidate flag for clinician review, not a
confirmed gap (note the "may be", "confirm", "mentioned" hedging in every gap
string above). A module-level `LIMITATIONS` docstring/constant in `rules.py`,
and a short note in the service's documentation, state plainly that keyword
triggers:

- do not handle negation ("patient denies tobacco use" still fires
  `TOBACCO_CESSATION`),
- do not verify age, interval, or whether the screening was already done, which
  matters most for `LIPID_SCREENING`, whose cited grade B is scoped to adults
  40-75 at a specific calculated risk threshold that a keyword scan cannot
  evaluate,
- carry citations verified on a fixed date (`CITATIONS_VERIFIED_ON`) against
  guidelines that are revised on their own schedule, the ADA source annually,

so every gap requires human confirmation. This mirrors the prior-auth agent's
"suggestions for human review" discipline and the SOAP structurer's "never
invent findings" honesty. Robust context handling is explicitly the job of the
P5-3 embedding-based Care Gap Agent stretch goal and is out of scope here.

### 5. Confidence (minor honesty fix)

`services/agent_care_gap/app.py` currently sets
`confidence = 0.9 if gaps else 1.0`. The `1.0` for the no-gaps case overclaims:
a keyword scan cannot be certain a note contains no care gaps. Replace with a
single documented module-level constant (value `0.9`) used in both branches,
commented as a fixed deterministic-rule-match indicator rather than a calibrated
probability. This is the one `app.py` change in this task.

### 6. Keep the tech-design doc honest (`docs/TECH-DESIGN.md`)

Section 3.3 of `docs/TECH-DESIGN.md` documents the Care Gap item shape as
`{"gap": "", "rule_id": "", "evidence": ""}`, which the new `source` field makes
stale. Update that JSON snippet to include `source` so the published contract
matches `shared/schemas.py`. This is a one-line doc edit, not a code change, but
this is a transparency-focused project and a documented shape that no longer
matches reality is exactly the kind of quiet drift the project rules exist to
prevent.

## Testing

`tests/test_care_gap_rules.py` covers the exit criterion "unit tests for every
rule firing and not firing":

- For each of the four rules: one test with a note that fires it, one test with
  a note that does not fire it. (8 tests.)
- The two existing tests must be reconciled with the rename, or `make test`
  goes red. `test_diabetes_triggers_a1c_rule` asserts
  `rule_id == "A1C_OVERDUE"`, which no longer exists after the rename to
  `A1C_MONITORING`; update its assertion (or let it be superseded by the new
  per-rule firing test for `A1C_MONITORING` and delete it, to avoid two tests
  covering the same case). `test_clean_note_has_no_gaps` is kept as-is.
- Every fired gap carries a `source` that constructs a valid `CareGapSource`
  with all required fields populated (`organization`, `title`, `year`, `url`
  non-empty).
- A structural test iterating the rule table asserting every rule has a
  non-empty citation, so no future rule can be added uncited. This enforces the
  exit criterion in code, not just in this one change.

A separate small test (or an assertion in the structural test) confirms
`CareGapItem` round-trips with the nested `source` through
`model_dump()`/reconstruction, so the registry-logging path (which serializes
`output.model_dump()`) is exercised.

`make test` and `make lint` must be clean. New tests must fail against the
current placeholder rules before the change (e.g. a test asserting
`rule_id == "HTN_SCREENING"` fails while the code still says `"BP_FOLLOWUP"`),
so they are meaningful regression tests rather than tautologies against the new
code.

## Out of scope

- Negation or clinical-context detection (deferred to P5-3).
- Any LLM call in the Care Gap path (v1 is deterministic; the tech design's
  "LLM only for optional phrasing" note is a future option, not this task).
- Age/interval/already-done verification (impossible from the flat four-string
  SOAP note and out of scope for a keyword engine).
- Changes to any other agent, the orchestrator, or `shared/llm.py`.
- The `CareGapOutput` wrapper shape (unchanged; only `CareGapItem` gains a
  field).
