# P2-3: Coding and Eligibility Agent, Design

## Context

Phase 2 (`docs/ROADMAP.md`) builds three agent services behind an
orchestrator. The Coding and Eligibility Agent is the last of the three to
be built. Like the prior-auth agent before P2-1, `services/agent_coding/`
is unverified pre-git scaffolding: `agent.py`, `app.py`, a Dockerfile, and
nothing else. There are no tests for it.

P2-3's exit criteria from the roadmap:

> Done when a SOAP note yields a valid CodingOutput, codes are presented as
> suggestions for human review, and eligibility flags are structured
> booleans.

Per `docs/MODEL-EFFORT-GUIDE.md` line 64, this task is Opus 4.8 at xhigh
effort because it is the "hardest clinical domain."

## Problems with the current scaffold

**1. The same parsing anti-pattern P2-1 already fixed.**
`agent.py::run` strips code fences by hand
(`raw.strip().removeprefix("```json")...`) and calls a bare `json.loads`
followed by `CodingOutput(**...)`, with no error handling. The P2-1 design
(`docs/superpowers/specs/2026-07-14-p2-1-prior-auth-agent-design.md`, lines
130 to 133) explicitly deferred this to P2-3 and instructed that the
`PriorAuthError` pattern "should be reused there (a `CodingError`) rather
than re-derived."

**2. Suggested codes are checked against nothing.**
No ICD-10 or CPT vocabulary exists anywhere in the repo. The model is the
sole authority on whether a code it emits is real. Large language models
readily produce well-formed codes that do not exist, and the current
`CodeSuggestion` schema gives a reviewer no way to tell a real code from an
invented one. This is the failure mode the project exists to govern, sitting
unaddressed in the agent handling the hardest clinical domain.

**3. `eligibility_flag` has no definition.**
`shared/schemas.py::CodeSuggestion.eligibility_flag` is a bare boolean that
nothing defines. The agent's only input is a `SoapNote`. It receives no
payer, plan, or benefits data, so insurance eligibility is not a
determination it can make. An undefined boolean named `eligibility_flag`
invites the model to fabricate a conclusion it has no basis for, and a bare
boolean is unreviewable. P2-2 gave every `CareGapItem` an `evidence` field
for exactly this reason.

## Design

The organizing principle: **the model proposes codes, and the code decides
whether they are real.** P2-2 confined hallucination risk to authoring time
by keeping the care gap match deterministic. The analogue here is to make
code existence a deterministic lookup that the model cannot influence.

### 1. Vocabulary (`shared/icd10.py`, new)

ICD-10-CM is published by CMS in the public domain and can be redistributed.
CPT is proprietary to the AMA and licensed, so it cannot be vendored into
this public repository. That asymmetry is a hard constraint, and the design
states it rather than papering over it.

The vocabulary lives in `shared/` rather than `services/agent_coding/`
because it has two consumers: this agent, and P2-4's benchmark, which needs
it to compute any verified-code metric.

```python
VOCAB_VERSION = "ICD-10-CM FY2026"   # recorded in every CodingOutput
VOCAB_PATH    = Path("data/vocab/...")
VOCAB_SHA256  = "..."                # pinned

@lru_cache(maxsize=1)
def load_codes() -> frozenset[str]: ...

def normalize(code: str) -> str: ...
def is_valid(code: str) -> bool: ...
```

The file is committed gzipped, roughly 1 to 2 MB compressed for about 74,000
codes. Vendoring rather than downloading at build time keeps CI fully
offline and deterministic, which matters for a CI job that only just went
green in P1-6 and currently has no external network dependency.

`load_codes` verifies the file against `VOCAB_SHA256` and raises if it does
not match. This makes the integrity boundary executable rather than a
comment. It is the same lesson as the LLM cache key: the vocabulary version
is part of any metric computed on top of it, so if the code list silently
changes between two P2-4 benchmark runs, the verified-code rate moves for
reasons that have nothing to do with the model under test. `VOCAB_VERSION`
is carried on every `CodingOutput` so any stored result is traceable to the
vocabulary that produced it.

`normalize` is load-bearing and easy to overlook. CMS stores codes without
the decimal point (`E119`), while models emit the dotted display form
(`E11.9`). Normalization strips whitespace, uppercases, and removes the dot
on both sides of the comparison. Without it every real code would be
reported as a hallucination and the metric would be exactly inverted.

`lru_cache` keeps the roughly 74,000 line parse to once per process rather
than once per request.

### 2. Schema (`shared/schemas.py`)

```python
class CodeSuggestion(BaseModel):
    system: Literal["ICD-10", "CPT"]
    code: str
    description: str
    vocabulary_status: Literal["verified", "not_found", "unchecked"] = "unchecked"
    eligibility_flag: bool = False
    eligibility_reason: str | None = None
```

`vocabulary_status` is a three-state literal, not a boolean, and the third
state is the point. `not_found` means the code was checked against the
pinned vocabulary and is not in it, which is the hallucination signal.
`unchecked` means no licensed vocabulary is available to check against,
which is true of every CPT code. Collapsing these into one boolean would let
CPT codes inflate the unverified count without being evidence of
hallucination, corrupting the exact metric this design exists to produce.

Invalid codes are returned rather than dropped. A reviewer sees everything
the model said plus whether each code is real, and the hallucination rate
becomes a first-class measurable for P2-4 and for Phase 3 drift detection.
Dropping them would leave a reviewer reading the artifact alone unable to
tell that the model hallucinated.

`eligibility_flag` keeps its name, since both the roadmap and the agent's
own name say eligibility, but gains a precise definition recorded in the
schema docstring: **this code is commonly subject to payer coverage or
medical-necessity review, or the note's documentation may not support it.**
That is assessable from a note alone. Whether a specific patient's plan
covers a service is not, and the agent does not claim to answer it.

A `model_validator` enforces that `eligibility_flag=True` requires a
non-empty `eligibility_reason`. This makes reviewability a schema guarantee
rather than a prompt convention, and mirrors `CareGapItem.evidence`.

```python
class CodingOutput(BaseModel):
    agent_name: Literal["coding"] = "coding"
    codes: list[CodeSuggestion]
    confidence: float = Field(ge=0.0, le=1.0)
    vocabulary_version: str

    @computed_field
    @property
    def not_found_count(self) -> int: ...
```

`not_found_count` is a `computed_field`, not stored state, so it cannot
disagree with `codes`. Being computed does not cost anything downstream:
pydantic serializes computed fields, so the count still lands in the
`agent_decisions` `output` JSONB column and is queryable without unpacking.
Pydantic is pinned at 2.10.4 in `requirements.txt`, which supports both
`computed_field` and `model_validator`.

### 3. Agent (`services/agent_coding/agent.py`)

Adopt the prior-auth structure wholesale rather than re-deriving it:

- Add `CodingError(ValueError)` shaped like
  `services/agent_prior_auth/agent.py::PriorAuthError`: takes `reason` and
  `raw`, stores a 200-character truncated preview so failures are
  diagnosable without dumping full model output into logs.
- Replace manual fence-stripping and bare `json.loads` with
  `shared.llm.extract_json`.
- Carry all three guards, including the one that is easy to miss:
  `MalformedJSONError` from `extract_json`, an `isinstance(data, dict)`
  check before construction (a bare JSON array raises `TypeError`, which
  neither of the other two handlers catch), and `ValidationError` from
  constructing `CodingOutput`. Each re-raises as `CodingError`.

Then the step that is specific to this task. After parsing succeeds, the
agent recomputes `vocabulary_status` for every code from its own lookup and
**overwrites whatever the model supplied.** ICD-10 codes resolve to
`verified` or `not_found`; CPT codes are always set to `unchecked`. If the
model were permitted to set this field, it could certify its own
hallucinations, which would make the entire design unenforced. The system
prompt is updated to tell the model not to emit the field, but the overwrite
is what guarantees it.

The `code` string is stored as the model emitted it, trimmed and uppercased,
so display keeps the conventional dotted form. Only the lookup is
normalized.

`log_decision` continues to be called on success only, unchanged. The
richer output flows into the existing `output` JSONB column with no registry
change.

No retry loop on malformed JSON. The reasoning is identical to P2-1's: the
retry in P1-2 structuring was justified by a measured rate (1 malformed
sample in a 120-note run), and no equivalent measurement exists for this
agent. If malformed output proves common, a bounded retry mirroring
`MAX_JSON_ATTEMPTS` is a follow-up.

### 4. Endpoint (`services/agent_coding/app.py`)

Catch `CodingError` and raise `HTTPException(502, str(exc))`, matching the
convention in `services/intake/app.py` and
`services/agent_prior_auth/app.py`. A failure here is the model or the
pipeline breaking, not a bug in this service, and P2-6 needs a clean signal
to isolate one agent's failure from the other two.

### 5. Tests

`tests/test_coding_agent.py`, mirroring `tests/test_prior_auth_agent.py`.
The mock target trap documented in the P2-1 spec applies unchanged: because
`agent.py` does `from shared.llm import call`, the name is bound into
`services.agent_coding.agent` at import time, so `monkeypatch` must target
`services.agent_coding.agent.call` and
`services.agent_coding.agent.log_decision`, not the `shared.*` originals.

Parsing cases, carried over from P2-1:

- Happy path produces a valid `CodingOutput` and calls `log_decision` with
  the expected `encounter_id`, `note_id`, `agent_name="coding"`, `model`,
  `effort`, `confidence`, and `output`.
- An empty codes list round-trips to `CodingOutput(codes=[], ...)`.
- Malformed JSON raises `CodingError`.
- A JSON array instead of an object raises `CodingError`.
- A confidence outside [0, 1] raises `CodingError`.
- A long raw response produces a truncated error preview.

Cases specific to this task, which carry the real weight:

- A real ICD-10 code resolves to `verified`.
- A fabricated ICD-10 code resolves to `not_found`, is **still present** in
  the returned codes, and is reflected in `not_found_count`.
- A CPT code resolves to `unchecked` and never to `not_found`. This guards
  the metric-corruption case directly.
- A mocked response that claims `vocabulary_status: "verified"` on a
  fabricated code is overridden by the agent. Without this test the trust
  boundary is unenforced, so this is the single most important test in the
  file.
- `eligibility_flag=True` with a missing or empty `eligibility_reason`
  raises `CodingError` via the wrapped `ValidationError` path.

`tests/test_icd10_vocab.py`:

- The vendored file's sha256 matches `VOCAB_SHA256`.
- A handful of known real codes are present.
- `normalize` maps `e11.9`, `E11.9`, and `E119` to the same key, and all
  three resolve identically through `is_valid`.
- A syntactically plausible but nonexistent code is absent.
- `VOCAB_VERSION` is non-empty and consistent with the vendored filename.

`tests/test_coding_app.py`, mirroring `tests/test_prior_auth_app.py`:

- `GET /health` returns `{"status": "ok", "service": "agent_coding"}`.
- `POST /run` happy path returns 200 with the expected body.
- `POST /run` where `run()` raises `CodingError` returns 502 with the reason
  in the detail.

### 6. Live verification

Before calling this task done, run the agent once against the real Anthropic
API with two real SOAP notes and capture both raw responses in the PR
description, following the P1-3 and P2-1 precedent. At least one suggested
ICD-10 code must resolve to `verified` against the vendored vocabulary,
which confirms the lookup path works end to end on real model output rather
than only on fixtures.

## Implementation risk stated up front

The exact CMS filename, file format, delimiter, and checksum have **not**
been verified. They are not asserted from memory. Confirming the real
published file and pinning its hash is the first implementation step, and if
the format differs from the two-column layout assumed here, the loader
changes accordingly. This is flagged rather than discovered later.

## Out of scope

- **No labeled coding gold set exists, and P2-3 does not create one.**
  Neither ACI-Bench nor PriMock57 carries ICD-10 or CPT ground truth, so the
  "held-out coding set" that P2-4 is written against
  (`docs/ROADMAP.md` line 51) does not exist today. P2-4 must choose between
  label-free metrics (verified-code rate, cross-model agreement), which this
  design makes computable at no labeling cost, or commissioning real labels.
  Assigning diagnostic codes is credentialed work, and self-generated labels
  would carry unmeasured error directly into a headline metric. That
  decision belongs to P2-4, which is budgeted at max effort for exactly this
  kind of reasoning.
- No CPT vocabulary, for the AMA licensing reason above.
- No change to `shared/llm.py::ROUTING`. P2-4 selects the coding model.
- No billable-versus-header code distinction. The CMS order file carries
  that flag and could refine validation later.
- No retry loop, per the reasoning in section 3.
- No Dockerfile or Kubernetes manifest changes; P2-5 covers containerization.

## Documentation to update

`docs/TECH-DESIGN.md` line 114 shows the old `CodingOutput` JSON shape and
must be updated to include `vocabulary_status`, `eligibility_reason`,
`vocabulary_version`, and `not_found_count`.

## Testing

`make test` must stay green with the new test files included, and the new
tests must fail against the current scaffold before the change, so they are
meaningful regression tests rather than tautologies written against the new
code.
