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

### 1. Vocabulary (`shared/vocab.py`, new)

ICD-10-CM and HCPCS Level II are both published by CMS in the public domain
and can be redistributed, so both are vendored. CPT is proprietary to the
AMA and licensed, so it cannot be vendored into this public repository. That
asymmetry is a hard constraint, and the design states it rather than
papering over it. It is also the only reason any code ends up `unchecked`.

The module lives in `shared/` rather than `services/agent_coding/` because
it has two consumers: this agent, and P2-4's benchmark, which needs it to
compute any verified-code metric. It is named `shared/vocab.py` rather than
`shared/icd10.py` because it covers two code systems.

```python
REPO_ROOT     = Path(__file__).resolve().parents[1]
VOCAB_DIR     = REPO_ROOT / "data" / "vocab"

ICD10_PATH    = VOCAB_DIR / "..."
ICD10_SHA256  = "..."                # sha256 of the DECOMPRESSED content
HCPCS_PATH    = VOCAB_DIR / "..."
HCPCS_SHA256  = "..."

# Recorded in every CodingOutput; names both releases.
# Release identifiers are placeholders until the artifacts are pinned.
VOCAB_VERSION = "ICD-10-CM FY... + HCPCS Level II ..."

@lru_cache(maxsize=1)
def load_icd10() -> frozenset[str]: ...
@lru_cache(maxsize=1)
def load_hcpcs() -> frozenset[str]: ...

def normalize(code: str) -> str: ...
def _looks_like_cpt(code: str) -> bool: ...     # private on purpose
def classify(system: str, code: str) -> Literal["verified", "not_found", "unchecked"]: ...
def verified_rate(verified: int, not_found: int) -> float | None: ...
```

There is deliberately no `_looks_like_hcpcs`. An earlier draft had one and it
was a mistake worth recording: HCPCS Level II codes are a letter followed by
four digits (`J1885`), and **normalized ICD-10-CM codes have exactly that
shape too** (`M54.16` normalizes to `M5416`, `E11.65` to `E1165`). The two
are not separable by shape, so a shape-plus-label rule for HCPCS would have
let a fabricated ICD-10-shaped code declared `HCPCS` reach `unchecked` and
escape the denominator, reopening the precise hole the rest of this section
exists to close. Vendoring the real HCPCS release replaces that guess with a
lookup and removes the question.

**The HCPCS artifact must contain Level II only, and this is an acceptance
condition on the file rather than a detail of parsing it.** "HCPCS" formally
comprises Level I, which *is* CPT, and Level II, the alphanumeric codes for
drugs, supplies, and services. If the acquired CMS artifact carries Level I
entries, rule 2 would verify CPT codes by lookup. That silently contradicts
section 1a's claim that CPT cannot be verified here, dissolves the
`unchecked` bucket this design calls irreducible, and moves the metric,
all without any test failing. Confirm the file is Level II only before
pinning it, and if the only available distribution bundles both, filter to
Level II during the one-time conversion and record that in the provenance
note.

Both loaders must read their module-level path global **at call time**. The
default-argument form `def load_icd10(path=ICD10_PATH)` binds at definition
time, which silently defeats the monkeypatch in the tamper test in section 5
and turns that test back into the no-op it exists to prevent.

`ICD10_PATH` and `HCPCS_PATH` are derived from the module's own location,
following
`governance/heldout.py:30` (`REPO_ROOT = Path(__file__).resolve()
.parents[1]`). A bare `Path("data/vocab/...")` would be resolved against the
current working directory, so it would work in tests run from the repo root
and fail anywhere else, which is the kind of breakage that shows up first in
a container.

**The Dockerfile needs one `COPY` line, and this task takes that one line
out of P2-5's scope.** `services/agent_coding/Dockerfile` currently copies
`requirements.txt`, `shared/`, and `services/agent_coding/`, and neither
`docker-compose.yml` nor the k8s manifest mounts a volume. Without a change,
the loaders raise on the first request inside every container, so `/run`
fails always, and the symptom would surface during P2-5 as a readiness-probe
failure with nothing pointing back here. Add:

```dockerfile
COPY data/vocab/ data/vocab/
```

This is deliberately not "containerization work." P2-5 owns image and
manifest design; shipping a data file that the service cannot start without
is part of building the service. Leaving it to P2-5 would mean this task
knowingly delivers an agent that cannot run in the only way it is ever
deployed.

`classify` is the only public entry point for turning a suggestion into a
status, and there is deliberately no public `is_valid`. The module has two
consumers, this agent and P2-4, and the status mapping is the thing both
must agree on. If either re-derived it from a membership primitive, the two
could diverge, and the divergence would surface as a scoring difference
rather than an error, which is exactly the argument
`shared/llm.py::extract_json`'s docstring makes about keeping shared logic
in one place. A bare `is_valid` would also be actively misleading to a
caller holding a CPT code, since `is_valid("99213")` is `False` for a
perfectly real code. `_looks_like_cpt` is private for the same reason:
exposing it invites a caller to rebuild the routing from its parts.

**`classify` routes on the vocabulary first and on shape second, never on
the model's `system` label alone, and this closes an escape hatch through
the trust boundary.** The ordering matters and is easy to get wrong: an
implementer told only "route on shape" will write
`if icd_shape: lookup elif cpt_shape: unchecked`, which breaks rules 1 and
2. The rule 2 break is the more damaging one: a real HCPCS code is
letter-plus-four-digits, so it matches "icd_shape", gets looked up in
ICD-10-CM only, misses, and returns `not_found`. That silently throws away
the entire payoff of vendoring HCPCS. The naive rule
("if `system == "ICD-10"` then look it up, else `unchecked`") hands the
model the decision about whether its own code gets checked. A fabricated
code labelled `CPT` would never be looked up and never count as
`not_found`, so a model that mislabels systems, or drifts toward emitting
CPT, would score a better verified rate without hallucinating any less. The
design elsewhere is careful that the model cannot certify its own codes;
this would let it exempt them instead, which is the same hole with an extra
step.

The rule, operating throughout on the **normalized** string:

1. Look the code up in the ICD-10-CM set. Present means `verified`, whatever
   the model labelled it.
2. Otherwise look it up in the HCPCS Level II set. Present means `verified`,
   again whatever the model labelled it.
3. Otherwise, if the code has CPT shape (five digits, or four digits plus a
   trailing letter for Category II and III) **and** the model declared it
   `CPT`, return `unchecked`.
4. Otherwise return `not_found`.

Rules 1 and 2 are vocabulary lookups and ignore the declared system
entirely. Rule 3 is the only place a model-supplied label affects the
outcome, and it is guarded by a shape test that **is** genuinely disjoint
from ICD-10-CM and HCPCS: both of those always begin with a letter, while
both CPT shapes begin with a digit. That disjointness is what makes rule 3
safe where a shape rule for HCPCS was not.

A string present in both vendored sets returns `verified` either way, so the
lookup order in rules 1 and 2 never changes an outcome. It is fixed only so
the behaviour is deterministic.

**`system` accepts `"HCPCS"` as a third value, because rejecting an honest
label biases the sample.** `ModelCodeSuggestion.system` is a `Literal`, so
without it a model correctly labelling `J1885` as `HCPCS` would fail
`ModelCodingPayload` validation, which section 3 wraps into `CodingError`
and section 4 turns into a 502. Those failures would land specifically on
notes mentioning drugs and supplies, so any verified rate computed over
successful runs would be measured on a systematically skewed subset. That is
the same argument section 2 uses to reject a `model_validator` on
eligibility flags.

`classify` takes `system` as a plain `str` rather than the `Literal`, since
P2-4 may call it outside the agent's validated path. An unrecognised value
still gets both lookups, because rules 1 and 2 ignore the label entirely, so
a real code returns `verified` no matter what it was called. What an
unrecognised value cannot do is match rule 3's label test, so such a code is
`verified` if either lookup hits and `not_found` otherwise. It is never
`unchecked`.

Degenerate input (`""`, `"N/A"`, prose) falls to rule 4 and counts as
`not_found`. That is the right default, since a suggestion that does not
contain a code is a defect rather than something to excuse, but it means
`not_found` is not purely a count of invented codes.

Rule 3 requires shape and label to agree, so a fabricated ICD-10-shaped code
cannot buy exemption by claiming to be CPT: it fails both vocabulary lookups
and does not match the digit-leading CPT shape. The conjunction cuts the
other way too, and the spec should not pretend otherwise: **a real CPT code
that the model mislabels as `"ICD-10"` fails both lookups, fails rule 3's
label test, and is counted `not_found`.** A real code is then scored as a
hallucination. That direction is conservative, in that it understates the
model's performance rather than flattering it, which is why the conjunction
is still the right call. It is a known distortion rather than a hidden one,
and section 5 tests both mislabel directions so the behaviour is pinned
rather than incidental.


Both files are committed gzipped. ICD-10-CM dominates at roughly 1 to 2 MB
compressed for about 74,000 codes; HCPCS Level II is far smaller, on the
order of thousands of codes. Vendoring rather than downloading at build time
keeps the test path
fully offline and deterministic. Note the precise claim: `ci.yml` already
installs from PyPI, so CI is not network-free overall. What vendoring buys
is that no metric-bearing lookup depends on a live CMS endpoint.

Each loader verifies its file against the matching pin (`ICD10_SHA256`,
`HCPCS_SHA256`) and raises if it does not match. This makes the integrity
boundary executable rather than a comment. It is the same lesson as the LLM
cache key: the vocabulary versions are part of any metric computed on top of
them, so if either code list silently changes between two P2-4 benchmark
runs, the verified rate moves for reasons that have nothing to do with the
model under test. `VOCAB_VERSION` names **both** releases and is carried on
every `CodingOutput`, so any stored result is traceable to the exact pair of
vocabularies that produced it. A single combined string is enough only if it is
maintained as one: **bump `VOCAB_VERSION` on any change to either pin.**
That is an obligation on this repo, not a property of the releases, which
move on different cadences (ICD-10-CM annually with the fiscal year, HCPCS
Level II quarterly). Two pins behind one version string is fine; two pins
behind a stale version string silently breaks traceability.

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

**Append this after line 18, not before `data/*`.** Order is not cosmetic
here: negations placed above the `data/*` line are overridden by it and the
file stays ignored, failing exactly as silently as having no rule at all.
`!data/vocab/` is the line that does the work, since it re-includes the
directory so git will descend into it; `!data/vocab/**` is belt and braces
and does nothing on its own.

Without this the vendored files silently never get committed, the loaders
raise in CI, and the natural fix is to download at build time, which
reintroduces exactly the non-determinism this design exists to prevent. The
existing rule is written to keep clinical data out of the repository; the
ICD-10-CM and HCPCS Level II code lists are public-domain reference tables
containing no patient information, so the exception is consistent with the
rule's intent rather than a weakening of it.

`normalize` is load-bearing and easy to overlook. CMS stores ICD-10-CM codes
without the decimal point (`E119`), while models emit the dotted display
form (`E11.9`). Normalization strips whitespace, uppercases, and removes the
dot on both sides of the comparison. Without it every real ICD-10 code would
be reported as a hallucination and the metric would be exactly inverted.
HCPCS Level II codes carry no decimal, so for them normalization is only the
strip-and-uppercase, but the same function is used on both paths so there is
one definition of a lookup key rather than two.

`lru_cache` keeps each parse to once per process rather than once per
request, and the two loaders cache independently.

### 1a. What `not_found` actually measures, and its floor

`not_found` is **not** a clean count of invented codes, and treating it as
one would overstate what this design can support. It fires for at least four
distinct causes:

1. Genuinely fabricated codes, the signal of interest.
2. Real codes absent from the **pinned** releases. Models trained on earlier
   data will emit retired or since-revised ICD-10-CM and HCPCS codes that
   were valid when written.
3. Real CPT codes the model mislabels as `"ICD-10"` or `"HCPCS"`, per the
   conjunction above. Honestly labelled CPT codes reach `unchecked` instead.
4. Degenerate non-code strings, per rule 4.

Vendoring HCPCS removes what would otherwise have been the largest of these:
real drug and supply codes are now verified by lookup rather than counted
against the model.

Causes 2, 3, and 4 give the metric a **nonzero floor unrelated to
hallucination.** For a number feeding P2-4 and Phase 3 drift, that floor has
to be stated rather than discovered when a model looks worse than it is.

Stated to match section 2a exactly, in its direction and over its
denominator: what is computed is *the fraction of **checkable** suggested
codes that are present in the pinned CMS ICD-10-CM and HCPCS Level II
releases*, where checkable means everything not classified `unchecked`. It
is a rate of presence in two pinned releases, not of clinical correctness,
and its complement is not a clean hallucination count. Calling it a
hallucination rate without qualification would be an overclaim.

Section 6 requires eyeballing every `not_found` code from the live runs
specifically to see which of the four causes dominates in practice. That is
cheap, it happens once, and it is the difference between a number that can
be reported and one that cannot.

The residual exposure in the other direction, stated plainly: **any string
matching the CPT shape test** and labelled `CPT`, whether or not it is a
real CPT code, is `unchecked` and invisible to the metric. That is `99999`
as much as `9999F` or `0001T`. This is now the **only** exemption channel,
since vendoring HCPCS eliminated the other one, and it is irreducible
without a licensed CPT vocabulary. Section 2a's denominator is what keeps
the exclusion explicit rather than implied.

### 2. Schema (`shared/schemas.py`)

The schema is split in two, and the split is what makes the trust boundary
structural rather than a convention the agent has to remember to enforce.

**What the model is allowed to say:**

```python
class ModelCodeSuggestion(BaseModel):
    system: Literal["ICD-10", "CPT", "HCPCS"]
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
state is the point. `not_found` means the code was looked up in both pinned
vocabularies and found in neither, which carries the hallucination signal
along with the floor described in section 1a. `unchecked` means the code was
not verifiable here at all: it both looks like CPT and was declared CPT, and
CPT is the one system whose vocabulary cannot be vendored. Note the
precision, since two earlier drafts got this wrong. `unchecked` is not a
synonym for "CPT," because a real CPT code the model mislabels as `ICD-10`
lands in `not_found` instead. And it is not "no vocabulary available"
generally, because HCPCS has a vendored vocabulary and is verified by
lookup. Collapsing the three states into one boolean would let unverifiable
CPT codes inflate the unverified count without being evidence of
hallucination, corrupting the exact metric this design exists to produce.

Unrecognised codes are returned rather than dropped. A reviewer sees
everything the model said plus whether each code was found, and the
verified rate becomes a first-class measurable for P2-4 and for Phase 3
drift detection. Dropping them would leave a reviewer reading the artifact
alone unable to tell that anything was wrong.

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

**One name, used everywhere: the verified rate.** Earlier drafts drifted
between "hallucination rate" and "verified rate," which is worse than
pedantic because the two run in opposite directions and section 1a shows
`not_found` is not a clean hallucination count anyway. This spec defines and
uses `verified_rate` only.

Section 2 argues that a three-state literal protects that metric. The
argument is only complete if the metric itself is written down,
so P2-4 does not have to infer it:

```
verified_rate = verified / (verified + not_found)
```

`unchecked` is excluded from **both** numerator and denominator. The naive
formula `not_found_count / len(codes)` is wrong, and wrong in a way that is
easy to miss: `unchecked` codes sit in `len(codes)` while being unverifiable
by construction, so that ratio moves whenever the mix of checkable and
uncheckable suggestions in a note shifts, which is a property of the note
rather than of the model. Two models could hallucinate at identical rates
and score differently.

Note the precision, since an earlier draft of this section got it wrong:
`unchecked` is not a synonym for "CPT," and not every unvendored system is
`unchecked`. A real CPT code mislabelled `ICD-10` lands in `not_found`, and
HCPCS codes are verified by lookup rather than exempted. The exclusion above
is defined on the status, never on the declared system.

**Across a run, pool the counts; do not average per-note rates.** P2-4
computes one rate over the whole held-out set by summing `verified_count`
and `not_found_count` across every note and dividing once. Averaging
per-note rates is a different number, and a worse one: it weights a note
with a single ICD-10 code the same as a note with twelve, and it has no
defined value for the notes that need it least.

**An empty denominator is undefined, and must be reported as undefined
rather than as 0.0.** A note yielding only CPT codes has zero `verified` and
zero `not_found`, so its rate is 0/0. Silently coercing that to 0.0 would
report a perfect score for a note where nothing was checked, which is the
most misleading possible reading. Pooling makes this rare at the run level,
but the run-level denominator can still be zero on a degenerate set, and the
harness must say so rather than print a number.

**These two rules ship as code, not only as prose.** `shared/vocab.py`
exposes the helper from section 1:

```python
def verified_rate(verified: int, not_found: int) -> float | None:
    """None when the denominator is zero. Never 0.0 in that case."""
```

Leaving the formula, the pooling rule, and the zero-denominator rule to
P2-4 prose would contradict this spec's own argument for `classify`: shared
logic that two consumers must agree on belongs in one place, because
divergence surfaces as a scoring difference rather than an error. A
four-line function with two tests removes the possibility. P2-4 sums
`verified_count` and `not_found_count` across the held-out set and calls
this once. The `float | None` return is what forces the caller to handle the
undefined case rather than let a silent 0.0 through.

To be explicit for whoever reviews the diff: **`verified_rate` ships with
zero callers inside the repository.** Its only consumer is P2-4, which does
not exist yet. That is intentional, not dead code, and it is the same
tradeoff as `classify` being written to serve a caller that has not been
built. The alternative is P2-4 re-deriving the rule and diverging silently,
which is the failure this spec keeps arguing against.

`CodingOutput` exposes both counts as computed fields, so the denominator is
reconstructible from a stored `agent_decisions` row without re-parsing the
code list:

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

- Each `ModelCodeSuggestion` becomes a `CodeSuggestion` whose
  `vocabulary_status` is the return value of
  `shared.vocab.classify(system, code)`, and nothing else. The agent does
  not branch on `system` itself. Routing on the declared system here is the
  escape hatch section 1 exists to close, and re-deriving the rules in the
  agent would put two copies of them in the codebase.
- Any suggestion with `eligibility_flag=True` and a missing or blank
  `eligibility_reason` has its flag degraded to `False`, per section 2.
  "Blank" means `None` or empty **after stripping whitespace**, so a reason
  of `"   "` degrades rather than counting as substantiation.
- `confidence` carries across from `ModelCodingPayload` unchanged. It is the
  model's to supply, unlike the two vocabulary fields.
- `vocabulary_version` is set from `shared.vocab.VOCAB_VERSION`.

Because the model's response was never parsed into a schema carrying these
fields, this is construction rather than correction. There is no window in
which a model-supplied `vocabulary_status` exists and has to be remembered
about.

The stored `code` string keeps the conventional dotted display form. Only
the lookup is normalized, and the two must not be conflated: `normalize`
strips the dot, so passing the stored value through it would destroy exactly
the formatting this preserves. Concretely, the agent stores
`code.strip().upper()` and passes the same string to `classify`, which does
its own normalization internally. There is deliberately no shared helper for
"trim and uppercase but keep the dot," because the only caller is this one
line and naming it would invite someone to reach for it instead of
`normalize` at lookup time.

**`TruncatedResponseError` must be caught, and this agent is the one most
likely to raise it.** `shared/llm.py:131` raises it when `stop_reason ==
"max_tokens"`, and it subclasses `RuntimeError` (line 53), not `ValueError`,
so a bare `except CodingError` in `app.py` misses it entirely and FastAPI
returns a 500. Section 4 argues P2-6 needs a clean per-agent failure signal,
and this is precisely the failure that would break it. `run()` catches
`TruncatedResponseError` and re-raises it as `CodingError`, so truncation
surfaces as a 502 like every other model-side failure. Pass `raw=""` when
doing so: `call()` raises before returning any text, so there is no raw
response to preview, and the implementer should not invent one. The reason
string carries the diagnosis in this case. The prior-auth agent
carries this same hole; fixing it there belongs to a follow-up rather than
this task, and it is recorded in "Known tracked debt" instead of being fixed
silently across a service this task does not own.

**Set `max_tokens` for this component deliberately now, and require P2-4 to
put it in the cache key.** The default is 1500 (`shared/llm.py:108`), and a
full ICD-10 plus CPT list with descriptions and eligibility reasons is the
largest output of the three agents, so truncation is a live risk here in a
way it is not for prior-auth.

The hazard is the opposite of an orphaned cache, and worth stating
precisely because the intuitive version is wrong.
`governance/llm_cache.py::cache_key` takes `(task, model, prompt_version,
payload)` and does **not** include `max_tokens`. Exactly one call site folds
it in, by convention: `governance/structuring_eval.py:82` builds
`version = f"{effort}|{hash_prompt(SYSTEM_PROMPT)}|max{MAX_TOKENS}"`.
`governance/facts.py:37` and `governance/judge.py:34-35` do not; they pass a
bare `PROMPT_VERSION = "v1"` literal that a human has to remember to bump.
No agent service imports `llm_cache` at all today.

So raising `max_tokens` later does not orphan coding results, because there
are none to orphan. The real risk lands on P2-4: if it caches coding calls
following the `facts.py` and `judge.py` pattern, `max_tokens` will not be in
the key, and changing it mid-benchmark produces silent cache **hits** that
blend two configurations into one number. That is precisely the failure
`llm_cache.py`'s own module docstring says the key exists to prevent, and
the weaker call sites do not currently prevent it.

Two requirements follow. Choose the value now, while nothing depends on it,
and record the number and its rationale in the code. And when P2-4 adds
caching for coding calls, its `prompt_version` must include `max_tokens` and
the prompt hash, following the `structuring_eval.py` pattern rather than the
`facts.py` one.

Sequencing, since the obvious reading is circular: run the section 6 live
verification at `max_tokens=4000`, deliberately generous so the observation
is not itself truncated, then pin the shipped value at **twice the largest
observed output token count, rounded up to the nearest 500, and never below
2000.** Section 6 cannot report usage at a cap that section 3 has not chosen
yet, so the verification cap and the shipped cap are two different numbers
and both are written down here rather than left to judgment.

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
- A genuine CPT-shaped code declared as CPT resolves to `unchecked` and
  never to `not_found`. This guards the metric-corruption case directly.
- **A fabricated ICD-10-shaped code mislabelled as `system: "CPT"` still
  resolves to `not_found`.** This is the escape-hatch test from section 1.
  Without it, the trust boundary has a documented bypass and the verified
  rate can be gamed by relabelling rather than by improving.
- A real ICD-10 code mislabelled as `CPT` still resolves to `verified`,
  confirming rule 1 consults the vocabulary before considering the declared
  system.
- **A real CPT code mislabelled as `"ICD-10"` resolves to `not_found`.**
  This pins the conservative distortion admitted in section 1a. It is the
  unsafe direction of the mislabel pair, and testing only the safe one would
  leave the spec claiming a property it never checks.
- A mocked response that claims `vocabulary_status: "verified"` on a
  fabricated code still comes back `not_found`. The claim is dropped by
  `ModelCodingPayload` rather than corrected afterwards, but the observable
  behaviour is what matters and it must be pinned: without this test the
  trust boundary is unenforced, so this is the single most important test in
  the file.
- A response mixing verified, not-found, and `unchecked` CPT codes produces
  `verified_count` and `not_found_count` that match the intended metric
  definition in section 2a, with `unchecked` excluded from both.
- **A suggestion with `system: "HCPCS"` flows end to end**: it is accepted by
  `ModelCodingPayload` rather than raising, and a real HCPCS code comes back
  `verified`. This is the direct regression test for the 502-and-biased-sample
  failure that admitting `"HCPCS"` exists to prevent, and no agent-level test
  would otherwise touch the third `system` value at all.
- `vocabulary_version` on the returned output equals
  `shared.vocab.VOCAB_VERSION`, and no model-supplied value can change it.
- `eligibility_flag=True` with a missing or blank `eligibility_reason` is
  degraded to `False` on that suggestion, while **every other code in the
  same response survives unchanged**. The second half is the point: it is
  the regression test against reintroducing whole-output rejection and the
  sampling bias described in section 2.
- `eligibility_flag=True` with a real reason is preserved as-is.

`tests/test_vocab.py`:

- The sha256 of the **decompressed** content of each vendored file matches
  its pin (`ICD10_SHA256`, `HCPCS_SHA256`), per section 1.
- **Each loader raises when the content does not match its pin**, and the
  test must be written so it genuinely exercises the gate. Both loaders are
  `@lru_cache`d over a module-level path, so the test monkeypatches
  `ICD10_PATH` (or `HCPCS_PATH`) to a tampered fixture and calls
  `load_icd10.cache_clear()` **both before and after**. Without the clear
  beforehand it reads an already-cached good set and passes without touching
  the verification, which is exactly the no-op failure this test exists to
  catch; without the clear afterwards it poisons every later test in the
  session. The sha256 test above only proves the pin was transcribed
  correctly and would pass against an implementation whose verification is
  missing entirely. Since section 1 claims this gate is what keeps the
  vocabularies from changing silently between two P2-4 runs, it is the one
  behaviour here that must not be taken on trust.
- Both vendored files are actually tracked by git, not merely present on disk.
  This is cheap (`git ls-files --error-unmatch`) and it is the one failure
  the `.gitignore` rule in section 1 exists to prevent, which would
  otherwise pass locally and fail only in a fresh clone or in CI. The test
  **skips** rather than fails when either the `.git` directory or the `git`
  binary is absent, since a source tarball or a Docker build context is a
  legitimate place to run the suite with neither.
- `classify` covers every branch of the four-rule algorithm: `verified` for
  a real ICD-10 code and for a real HCPCS code, each regardless of the
  declared system; `not_found` for a fabricated ICD-10-shaped code declared
  `CPT`; `not_found` for a real CPT code declared `ICD-10`; and `unchecked`
  only when shape and declared system both say CPT. These are the unit-level
  counterparts of the agent tests above, and they belong here because
  `classify` is the function P2-4 will also call.
- For an **unrecognised** `system` value, `classify` returns `verified` when
  the code is in either vendored set and `not_found` only when it is in
  neither. The `not_found` case must use a code absent from both
  vocabularies. Asserting `not_found` for an unrecognised system with a real
  code would pin the opposite of the intended behaviour, since rules 1 and 2
  ignore the label entirely.
- A CPT-shaped code declared `"HCPCS"` returns `not_found`, not `unchecked`.
  This is the other half of rule 3's label guard, and only the ICD-10
  mislabel direction is covered otherwise.
- **A fabricated ICD-10-shaped code declared `"HCPCS"` returns `not_found`.**
  This is the specific hole an earlier draft opened with a shape-based HCPCS
  rule, and the reason HCPCS is vendored rather than shape-matched. Use a
  code shaped like a normalized real one (letter plus four digits, e.g.
  `M9999`) so the test would actually fail under the rejected design.

Every hard-coded code in these tests, the real ones and the fabricated ones
alike, must be confirmed present or absent against the **pinned** releases
at implementation time. `M99` is a populated ICD-10-CM category, so a
plausible-looking fabrication can turn out to be real, and a test asserting
`not_found` on a genuine code would pin the wrong behaviour while looking
correct.
- `classify` returns `not_found` for degenerate input (`""`, `"N/A"`), so
  the documented behaviour in section 1 is pinned rather than incidental.
- `verified_rate` returns the pooled ratio for non-zero denominators, and
  **`None`, never 0.0, when `verified + not_found == 0`.** The second case
  is the whole reason the helper exists rather than an inline division.
- `verified_rate` on a pooled pair differs from the mean of per-note rates
  on a set constructed so the two diverge. This pins the pooling rule from
  section 2a as behaviour rather than prose.
- A handful of known real codes are present in each set, including at least
  one HCPCS code (`J1885`, `G0008`).
- `normalize` maps `e11.9`, `E11.9`, and `E119` to the same key, and all
  three produce the same `classify` result.
- A syntactically plausible but nonexistent code is absent.
- `VOCAB_VERSION` is non-empty and both `ICD10_PATH.is_file()` and
  `HCPCS_PATH.is_file()`. The looser "consistent with the vendored filename"
  is not assertable while the filenames are deliberately unpinned, so the
  test checks what is knowable now.

`tests/test_schemas.py`, the repo's existing contract-test file for
`shared/schemas.py`, gains a direct schema-level test that
`ModelCodeSuggestion` drops an extra `vocabulary_status` key. The agent test
above pins the observable behaviour, but it does so through pydantic's
`extra="ignore"` default, which is configuration-sensitive. If a base model
config ever set `extra="allow"`, the model would regain a channel to
certify its own codes and the agent test might still pass. A three-line
schema test catches that directly and cheaply.

The existing `tests/test_schemas.py:26` case, which constructs
`CodeSuggestion(system="LOINC", ...)` inside `pytest.raises(ValidationError)`,
still passes after this change but for a partly different reason: the newly
required `vocabulary_status` would also make it raise. Give it an explicit
`vocabulary_status` so it continues to test the bad-`system` rejection it
was written for rather than passing by accident.

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

Two further things must be recorded from these runs, because both are cheap
here and expensive later:

- **Classify every `not_found` code by cause**, using the four categories in
  section 1a: fabricated, real but absent from the pinned releases, a real
  CPT code the model mislabelled, or degenerate input. This is the only
  measurement of the metric's floor that this task produces, and without it
  P2-4 cannot tell a model that hallucinates from one that simply predates
  the pinned releases.
- **Record the observed output token counts**, which is what section 3's
  `max_tokens` value gets pinned from. Run these verifications at a
  deliberately generous cap so the observation is not itself truncated.

## Implementation risk stated up front

The exact CMS filenames, file formats, delimiters, and checksums have **not**
been verified, for either vocabulary. They are not asserted from memory.
Confirming the real published files and pinning their hashes is the first
implementation step, and if a format differs from the two-column layout
assumed here, that loader changes accordingly. This is flagged rather than
discovered later. HCPCS Level II in particular is published in a different
form from ICD-10-CM and should not be assumed to match it.

The blast radius is bounded: each loader returns a `frozenset[str]` and
`normalize` operates on a single code, so both APIs and every consumer are
format-independent. Only the parser body changes.

Two bounds on the deferral, so it cannot quietly expand:

- **Size ceiling.** If the vendored artifacts exceed roughly 5 MB compressed
  in total, stop and revisit rather than committing them. The vendoring
  argument in section 1 assumes files small enough that repository weight is
  a non-issue, and that assumption should be checked rather than discovered
  after the commit. HCPCS Level II is far smaller than ICD-10-CM, on the
  order of thousands of codes rather than tens of thousands, so ICD-10-CM
  dominates the budget.
- **Format fallback.** If the only published form is XLSX, or a flat file
  nested inside a ZIP, do not add a runtime dependency on an office-format
  reader or unzip at import time. Convert once during implementation, vendor
  the resulting flat delimited file, and record the provenance and the
  conversion command in a short note beside the data so the artifact is
  reproducible from the CMS original.

**Sequencing.** Acquiring, verifying, and pinning the vocabulary is the
least predictable part of this task and is independent of the agent rewrite.
It should be the first unit in the implementation plan, with the schema,
agent, and endpoint work gated behind a confirmed hash, so a surprise in the
CMS distribution surfaces before any dependent code is written.

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
- No Kubernetes manifest changes, and no Dockerfile changes **beyond the
  single `COPY data/vocab/` line** carved out in section 1. P2-5 still owns
  image and manifest design; this task owns shipping the data file its own
  service cannot start without.
- No CPT vocabulary, and therefore no way to verify a CPT-shaped code
  declared CPT. This is the single irreducible `unchecked` bucket and the
  only remaining exemption channel, per section 1a.
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
- **`governance/facts.py` and `governance/judge.py` pass a bare
  `PROMPT_VERSION = "v1"` literal as their cache `prompt_version`**, while
  `governance/structuring_eval.py:82` folds in effort, a prompt hash, and
  `max_tokens`. The two weak call sites depend on a human remembering to
  bump a string by hand. Editing a judge prompt without bumping it yields
  silent cache hits blending two prompt versions into one number, which is
  the exact failure `llm_cache.py`'s module docstring says the key exists to
  prevent. Found while verifying the `max_tokens` claim in section 3. Not
  P2-3's to fix, and it does not affect this task, but it sits directly
  under the Phase 1 headline metric and belongs in Phase 3 governance work.

## Documentation to update

- `docs/TECH-DESIGN.md` line 114 shows the old `CodingOutput` JSON shape and
  must be updated to include `vocabulary_status`, `eligibility_reason`,
  `vocabulary_version`, `verified_count`, and `not_found_count`, and to
  record that `system` now accepts `"HCPCS"` alongside `"ICD-10"` and
  `"CPT"`.
- `.gitignore` needs the `data/vocab/` exception from section 1. This is a
  functional change, not documentation, and without it the vendored
  vocabulary is silently never committed.

## Testing

`make test` must stay green with the new test files included, and the new
tests must fail against the current scaffold before the change, so they are
meaningful regression tests rather than tautologies written against the new
code.
