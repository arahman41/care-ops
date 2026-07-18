# P2-3: Coding and Eligibility Agent, Design

## Context

Phase 2 (`docs/ROADMAP.md`) builds three agent services behind an
orchestrator. The Coding and Eligibility Agent is the last of the three to
be built. Like the prior-auth agent before P2-1, `services/agent_coding/`
is unverified pre-git scaffolding: `agent.py`, `app.py`, `__init__.py`, a
`requirements.txt`, and a Dockerfile, none of it exercised. There are no
tests for it.

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
VOCAB_SHA256  = "..."                # sha256 of the DECOMPRESSED content

@lru_cache(maxsize=1)
def load_codes() -> frozenset[str]: ...

def normalize(code: str) -> str: ...
def is_valid(code: str) -> bool: ...
```

The file is committed gzipped, roughly 1 to 2 MB compressed for about 74,000
codes. Vendoring rather than downloading at build time keeps the test path
fully offline and deterministic. Note the precise claim: `ci.yml` already
installs from PyPI, so CI is not network-free overall. What vendoring buys
is that no metric-bearing lookup depends on a live CMS endpoint.

`load_codes` verifies the file against `VOCAB_SHA256` and raises if it does
not match. This makes the integrity boundary executable rather than a
comment. It is the same lesson as the LLM cache key: the vocabulary version
is part of any metric computed on top of it, so if the code list silently
changes between two P2-4 benchmark runs, the verified-code rate moves for
reasons that have nothing to do with the model under test. `VOCAB_VERSION`
is carried on every `CodingOutput` so any stored result is traceable to the
vocabulary that produced it.

**The pin is the sha256 of the decompressed content, not of the gzip
bytes.** gzip output is not byte-stable across tools and platforms, since
the header carries an mtime and an OS byte, so pinning the compressed bytes
would let a harmless re-compression break the integrity gate while the code
list is completely unchanged. That would be a false alarm on the one
mechanism that is supposed to be trustworthy.

**`.gitignore` requires an explicit exception, and this is a blocker, not a
detail.** `.gitignore` lines 17 and 18 are `data/*` followed by
`!data/.gitkeep`. Because `data/*` excludes the `data/vocab` directory
itself, git never descends into it, so a negation naming a file inside it
cannot re-include the file. The directory must be re-included first:

```gitignore
# Public-domain reference vocabulary, not clinical data (see P2-3 spec)
!data/vocab/
!data/vocab/**
```

Without this the vendored file silently never gets committed, `load_codes`
raises in CI, and the natural fix is to download at build time, which
reintroduces exactly the non-determinism this design exists to prevent. The
existing rule is written to keep clinical data out of the repository; an
ICD-10-CM code list is a public-domain reference table containing no patient
information, so the exception is consistent with the rule's intent rather
than a weakening of it.

`normalize` is load-bearing and easy to overlook. CMS stores codes without
the decimal point (`E119`), while models emit the dotted display form
(`E11.9`). Normalization strips whitespace, uppercases, and removes the dot
on both sides of the comparison. Without it every real code would be
reported as a hallucination and the metric would be exactly inverted.

`lru_cache` keeps the roughly 74,000 line parse to once per process rather
than once per request.

### 2. Schema (`shared/schemas.py`)

The schema is split in two, and the split is what makes the trust boundary
structural rather than a convention the agent has to remember to enforce.

**What the model is allowed to say:**

```python
class ModelCodeSuggestion(BaseModel):
    system: Literal["ICD-10", "CPT"]
    code: str
    description: str
    eligibility_flag: bool = False
    eligibility_reason: str | None = None


class ModelCodingPayload(BaseModel):
    codes: list[ModelCodeSuggestion]
    confidence: float = Field(ge=0.0, le=1.0)
```

**What the agent returns:**

```python
class CodeSuggestion(ModelCodeSuggestion):
    vocabulary_status: Literal["verified", "not_found", "unchecked"]


class CodingOutput(BaseModel):
    agent_name: Literal["coding"] = "coding"
    codes: list[CodeSuggestion]
    confidence: float = Field(ge=0.0, le=1.0)
    vocabulary_version: str
```

Model output is parsed into `ModelCodingPayload`, never directly into
`CodingOutput`. This resolves two problems at once that an overwrite-based
approach only papers over:

1. `vocabulary_status` and `vocabulary_version` are not fields the model can
   populate, because they do not exist on the schema its response is parsed
   into. Pydantic's default `extra="ignore"` means a model that emits
   `vocabulary_status: "verified"` on a fabricated code has that key
   silently discarded. The model cannot certify its own hallucinations
   because it has no channel to make the claim.
2. `vocabulary_version` can be a required field on `CodingOutput` without
   breaking anything. Had the model payload been parsed directly into
   `CodingOutput`, a required `vocabulary_version` that no model ever emits
   would raise `ValidationError` on every single call, which section 3 wraps
   into `CodingError`, which section 4 turns into a 502. The agent would
   return 502 unconditionally and be dead on arrival.

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

An unsubstantiated eligibility flag is **degraded per suggestion, not
escalated to a failure of the whole call.** If the model sets
`eligibility_flag=True` and supplies no reason or a blank one, the agent
sets that one suggestion's flag back to `False` and leaves every other code
untouched.

The tempting alternative, a `model_validator` on `ModelCodeSuggestion` that
rejects a flag without a reason, is wrong here, and the reason is a metric
integrity problem rather than an ergonomic one. A validator raises
`ValidationError` for the entire payload, so a single unsubstantiated flag
would discard the whole `CodingOutput`, including every correctly validated
code in it. Those losses would surface as `CodingError`, indistinguishable
from a parse failure, and they would fall specifically on the runs
containing eligibility flags. Any verified-code rate computed over
successful runs would then be measured on a biased sample, and nothing in
the output would reveal it.

The cost of degrading is that the count of unsubstantiated flags is not
retained. That is accepted deliberately: no metric currently reads
eligibility flags, whereas the vocabulary numbers feed P2-4 and Phase 3
drift, so adding a counter for an unmeasured quantity would be unused state.
If eligibility ever becomes a measured surface, the counter is a small
follow-up.

### 2a. Defining the metric this produces

Section 2 argues that a three-state literal protects the hallucination
metric. That argument is only complete if the metric itself is written down,
so P2-4 does not have to infer it:

```
verified_rate = verified / (verified + not_found)
```

`unchecked` is excluded from **both** numerator and denominator. The naive
formula `not_found_count / len(codes)` is wrong, and wrong in a way that is
easy to miss: because every CPT code is permanently `unchecked`, that ratio
moves whenever the ICD-10 to CPT mix in a note shifts, which is a property
of the note rather than of the model. Two models could hallucinate at
identical rates and score differently.

`CodingOutput` therefore exposes both counts as computed fields, so the
denominator is reconstructible from a stored `agent_decisions` row without
re-parsing the code list:

```python
    @computed_field
    @property
    def verified_count(self) -> int: ...

    @computed_field
    @property
    def not_found_count(self) -> int: ...
```

Both are `computed_field`, not stored state, so they cannot disagree with
`codes`. Being computed costs nothing downstream: pydantic serializes
computed fields into `model_dump()`, so both land in the `agent_decisions`
`output` JSONB column and are queryable without unpacking the array.
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
  constructing `ModelCodingPayload`. Each re-raises as `CodingError`.

Then the enrichment step that is specific to this task. The parsed
`ModelCodingPayload` is mapped into the returned `CodingOutput`:

- Each `ModelCodeSuggestion` becomes a `CodeSuggestion` with
  `vocabulary_status` computed by the agent. ICD-10 codes resolve to
  `verified` or `not_found` via `shared.icd10.is_valid`; CPT codes are
  always `unchecked`.
- Any suggestion with `eligibility_flag=True` and a missing or blank
  `eligibility_reason` has its flag degraded to `False`, per section 2.
- `vocabulary_version` is set from `shared.icd10.VOCAB_VERSION`.

Because the model's response was never parsed into a schema carrying these
fields, this is construction rather than correction. There is no window in
which a model-supplied `vocabulary_status` exists and has to be remembered
about.

The `code` string is stored as the model emitted it, trimmed and uppercased,
so display keeps the conventional dotted form. Only the lookup is
normalized.

**`TruncatedResponseError` must be caught, and this agent is the one most
likely to raise it.** `shared/llm.py:131` raises it when `stop_reason ==
"max_tokens"`, and it subclasses `RuntimeError` (line 53), not `ValueError`,
so a bare `except CodingError` in `app.py` misses it entirely and FastAPI
returns a 500. Section 4 argues P2-6 needs a clean per-agent failure signal,
and this is precisely the failure that would break it. `run()` catches
`TruncatedResponseError` and re-raises it as `CodingError`, so truncation
surfaces as a 502 like every other model-side failure. The prior-auth agent
carries this same hole; fixing it there belongs to a follow-up rather than
this task, and it is recorded in "Known tracked debt" instead of being fixed
silently across a service this task does not own.

**Set `max_tokens` for this component deliberately now, before P2-4 runs.**
The default is 1500 (`shared/llm.py:108`), and a full ICD-10 plus CPT list
with descriptions and eligibility reasons is the largest output of the three
agents. Raising it later would not be free: `governance/llm_cache.py` keys
on `max_tokens`, so changing it invalidates every cached coding call and
orphans paid-for results, which is exactly how a prior run was lost. Right
now zero coding results are cached, so this is the only moment when the
value can be chosen at no cost. Pick it during implementation from the
observed token usage of the live verification runs in section 6, and record
the chosen number and its rationale in the code.

**The system prompt is rewritten to match the new payload.** The current
`_SYSTEM` string advertises the old shape, including `eligibility_flag` with
no reason field, so leaving it unchanged would make the degradation path in
section 2 fire constantly on flags the model was never asked to justify. The
new prompt:

- describes exactly `ModelCodingPayload`, with `eligibility_reason` present
  and documented as required whenever `eligibility_flag` is true,
- states the defined meaning of `eligibility_flag` from section 2, so the
  model is not left to invent one,
- says nothing about `vocabulary_status` or `vocabulary_version`, since
  those are not the model's to supply,
- keeps the existing instruction that codes are suggestions for human review
  and not confirmed codes, which is a roadmap exit criterion,
- asks for ICD-10 in conventional dotted form.

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

One more parsing case, new in this task:

- A mocked `call` that raises `TruncatedResponseError` produces a
  `CodingError`, not a bare `RuntimeError`. This is what keeps a truncated
  response a 502 rather than a 500.

Cases specific to this task, which carry the real weight:

- A real ICD-10 code resolves to `verified`.
- A fabricated ICD-10 code resolves to `not_found`, is **still present** in
  the returned codes, and is reflected in `not_found_count`.
- A CPT code resolves to `unchecked` and never to `not_found`. This guards
  the metric-corruption case directly.
- A mocked response that claims `vocabulary_status: "verified"` on a
  fabricated code still comes back `not_found`. The claim is dropped by
  `ModelCodingPayload` rather than corrected afterwards, but the observable
  behaviour is what matters and it must be pinned: without this test the
  trust boundary is unenforced, so this is the single most important test in
  the file.
- A response mixing verified, not-found, and CPT codes produces
  `verified_count` and `not_found_count` that match the intended metric
  definition in section 2a, with `unchecked` excluded from both.
- `vocabulary_version` on the returned output equals
  `shared.icd10.VOCAB_VERSION`, and no model-supplied value can change it.
- `eligibility_flag=True` with a missing or blank `eligibility_reason` is
  degraded to `False` on that suggestion, while **every other code in the
  same response survives unchanged**. The second half is the point: it is
  the regression test against reintroducing whole-output rejection and the
  sampling bias described in section 2.
- `eligibility_flag=True` with a real reason is preserved as-is.

`tests/test_icd10_vocab.py`:

- The sha256 of the **decompressed** vocabulary content matches
  `VOCAB_SHA256`, matching the pin defined in section 1.
- The vendored file is actually tracked by git, not merely present on disk.
  This is cheap (`git ls-files --error-unmatch`) and it is the one failure
  the `.gitignore` rule in section 1 exists to prevent, which would
  otherwise pass locally and fail only in a fresh clone or in CI.
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
- No counter for degraded eligibility flags, per the reasoning in section 2.
- **No fix to the identical `TruncatedResponseError` hole in
  `services/agent_prior_auth/agent.py`.** It is inherited from P2-1, not
  introduced here, and it lives in a service this task does not own. Fixing
  it silently across service boundaries would put an unreviewed behaviour
  change in a task scoped to a different agent. Recorded as tracked debt
  below instead.

## Known tracked debt this task records but does not fix

- `services/agent_prior_auth/agent.py` does not catch
  `TruncatedResponseError`, so a truncated prior-auth response returns 500
  rather than the 502 its own design specifies. Same root cause as the fix
  applied here, one service over. Worth folding into P2-6, which is where
  per-agent failure isolation actually gets exercised.

## Documentation to update

- `docs/TECH-DESIGN.md` line 114 shows the old `CodingOutput` JSON shape and
  must be updated to include `vocabulary_status`, `eligibility_reason`,
  `vocabulary_version`, `verified_count`, and `not_found_count`.
- `.gitignore` needs the `data/vocab/` exception from section 1. This is a
  functional change, not documentation, and without it the vendored
  vocabulary is silently never committed.

## Testing

`make test` must stay green with the new test files included, and the new
tests must fail against the current scaffold before the change, so they are
meaningful regression tests rather than tautologies written against the new
code.
