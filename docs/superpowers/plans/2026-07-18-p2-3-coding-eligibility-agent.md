# P2-3: Coding and Eligibility Agent Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the unverified `services/agent_coding/` scaffold into a working agent whose suggested codes are checked against vendored CMS vocabularies rather than taken on the model's word, with the parsing hardened the way P2-1 hardened prior-auth.

**Architecture:** A new `shared/vocab.py` holds two vendored CMS code sets (ICD-10-CM and HCPCS Level II) behind a single `classify(system, code)` entry point, so the agent and P2-4 cannot disagree about what a status means. `shared/schemas.py` splits the coding contract in two: `ModelCodingPayload` is what the model is parsed into and carries no vocabulary fields, `CodingOutput` is what the agent returns and carries them. That split is what makes the trust boundary structural: the model has no channel to claim its own code is verified. The agent parses into the former, enriches via `classify`, and constructs the latter.

**Tech Stack:** Python 3.12, pydantic 2.10.4, FastAPI, pytest. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-07-18-p2-3-coding-eligibility-agent-design.md`

**Model/effort:** per `docs/MODEL-EFFORT-GUIDE.md` line 64, P2-3 recommends `/model opus` and `/effort xhigh` ("hardest clinical domain"). Confirm the session matches before starting.

**Branch:** `p2-3-coding-agent` already exists and carries the spec commits. Work continues on it.

---

## Read this before Task 1

Two things in this plan are easy to get subtly wrong in ways no test would
catch unless the test is written exactly as specified.

**1. The model must never be able to set `vocabulary_status`.** The whole
design rests on it. The protection is that model output is parsed into
`ModelCodingPayload`, which has no such field, and pydantic's default
`extra="ignore"` discards it. Do not "simplify" by parsing straight into
`CodingOutput` and overwriting afterwards, and do not add
`model_config = ConfigDict(extra="allow")` anywhere in this chain.

**2. `classify` routes on the vocabularies first, then on shape, never on
the model's declared system alone.** The natural-looking
`if system == "ICD-10": lookup else: unchecked` hands the model the decision
about whether its own code gets checked, which is the exact hole the spec
spends section 1 closing. Rules 1 and 2 ignore the label entirely.

If either of these seems like unnecessary indirection while implementing,
re-read spec sections 1 and 2 before changing anything.

---

## Chunk 1: Vocabulary acquisition and `shared/vocab.py`

This chunk is sequenced first deliberately. Acquiring and pinning the CMS
artifacts is the least predictable part of the task, and every later chunk
depends on a confirmed hash. If the published files differ from what the
spec assumes, that surfaces here rather than after the agent is written.

### Task 1: Acquire, verify, and pin the two CMS artifacts

**This task needs network access and human judgment. It is not fully
automatable.** If you are an agent without network access, stop and hand
this task back rather than inventing a checksum or a filename.

**Files:**
- Create: `data/vocab/icd10cm_codes_<FY>.txt.gz`
- Create: `data/vocab/hcpcs_level2_<YEAR>.txt.gz`
- Create: `data/vocab/PROVENANCE.md`

- [ ] **Step 1: Download the ICD-10-CM code descriptions file from CMS**

Get the current fiscal year "Code Descriptions" file from the CMS ICD-10-CM
release page. The flat two-column form (code, then description) is what this
plan assumes.

- [ ] **Step 2: Download the HCPCS Level II file from CMS**

**Acceptance condition, per spec section 1: the artifact must contain Level
II only.** "HCPCS" formally includes Level I, which *is* CPT. If the
distribution bundles both levels, filter to Level II now and record the
filter in `PROVENANCE.md`. Shipping Level I entries would make `classify`
verify CPT codes by lookup, which dissolves the `unchecked` bucket and moves
the metric with no test failing.

- [ ] **Step 3: Check the format assumptions before writing any parser**

Confirm for each file: it is flat and delimited (not XLSX, not a nested
ZIP), codes are stored without decimal points, there is one code per line,
and the text encoding is UTF-8. If a file is XLSX or nested, convert it once
here, vendor the flat result, and record the exact conversion command in
`PROVENANCE.md`. Do not add an office-format reader or an unzip step to the
runtime path.

**Take the `codes` file, not the `order` file.** CMS publishes both
`icd10cm_codes_*.txt` (code, then description) and `icd10cm_order_*.txt`,
which leads each line with a five-digit sequence number. The Task 3 parser
takes the first whitespace-delimited token as the code, so an order file
would silently load 74,000 sequence numbers as codes. The sha256 pin would
not catch it, because the pin covers whatever was downloaded. Only
`test_known_real_codes_are_present` would fail, and it would look like a
vocabulary problem rather than a wrong-file problem.

CMS flat files are sometimes Windows-1252 rather than UTF-8. That fails
loudly at decode time rather than silently, but check it here so the parser
does not need a guess.

- [ ] **Step 4: Check the size ceiling**

Run: `du -ch data/vocab/*.gz | tail -1`
Expected: roughly 1 to 2 MB total. **If the total exceeds 5 MB compressed,
stop and raise it with the user** rather than committing, per the spec's
stated bound.

- [ ] **Step 5: Compute the two pins, over DECOMPRESSED content**

```bash
python - <<'PY'
import gzip, hashlib, pathlib
for p in sorted(pathlib.Path("data/vocab").glob("*.gz")):
    data = gzip.decompress(p.read_bytes())
    print(p.name, hashlib.sha256(data).hexdigest(), f"{len(data)} bytes")
PY
```

Hash the decompressed bytes, never the gzip bytes. gzip output is not
byte-stable across tools (the header carries an mtime and an OS byte), so a
harmless re-compression would break the gate with the code list unchanged.

- [ ] **Step 6: Write `data/vocab/PROVENANCE.md`**

Record for each file: source URL, download date, the release identifier
(fiscal year or quarter), the decompressed sha256, any conversion or
filtering command applied, and an explicit note that the HCPCS file is Level
II only. This is what makes the vendored artifact reproducible from the CMS
original.

- [ ] **Step 7: Commit (this will fail until Task 2; that is expected)**

Do not force the commit. Task 2 fixes `.gitignore` first.

---

### Task 2: Add the `.gitignore` exception

Without this the vendored files silently never get committed, the loaders
raise in CI, and the natural "fix" is to download at build time, which
reintroduces the non-determinism the vendoring exists to prevent.

**Files:**
- Modify: `.gitignore` (append after line 18)

- [ ] **Step 1: Confirm the files are currently ignored**

Run: `git check-ignore -v data/vocab/*.gz`
Expected: each path reported as ignored by the `data/*` rule on line 17.

- [ ] **Step 2: Append the exception AFTER line 18**

```gitignore
# Public-domain reference vocabularies, not clinical data (see P2-3 spec)
!data/vocab/
!data/vocab/**
```

**Order matters and is not cosmetic.** Negations placed above the `data/*`
line are overridden by it and the files stay ignored, failing exactly as
silently as having no rule at all. `!data/vocab/` is the line that does the
work, because `data/*` excludes the directory itself and git will not
descend into an excluded directory. `!data/vocab/**` is belt and braces.

- [ ] **Step 3: Verify the exception works and clinical data is still ignored**

Run: `git check-ignore -v data/vocab/*.gz ; git status --short data/`
Expected: the `.gz` files now appear as untracked, and nothing under
`data/aci-bench/`, `data/primock57/`, or `data/splits/` appears.

- [ ] **Step 4: Commit**

```bash
git add .gitignore data/vocab/
git commit -m "feat(P2-3): vendor CMS ICD-10-CM and HCPCS Level II vocabularies"
```

---

### Task 3: `normalize` and the two checksummed loaders

**Files:**
- Create: `shared/vocab.py`
- Create: `tests/test_vocab.py`

- [ ] **Step 1: Write the failing tests**

```python
import gzip
import pytest
from shared import vocab


def test_normalize_strips_dot_whitespace_and_case():
    assert vocab.normalize(" e11.9 ") == "E119"
    assert vocab.normalize("E11.9") == "E119"
    assert vocab.normalize("E119") == "E119"


def test_icd10_pin_matches_decompressed_content():
    import hashlib
    data = gzip.decompress(vocab.ICD10_PATH.read_bytes())
    assert hashlib.sha256(data).hexdigest() == vocab.ICD10_SHA256


def test_hcpcs_pin_matches_decompressed_content():
    import hashlib
    data = gzip.decompress(vocab.HCPCS_PATH.read_bytes())
    assert hashlib.sha256(data).hexdigest() == vocab.HCPCS_SHA256


def test_loader_raises_on_tampered_content(tmp_path, monkeypatch):
    """The integrity gate must actually run, not just be transcribed.

    Both loaders are lru_cached over a module-level path, so this test has
    to clear the cache on BOTH sides. Without the clear beforehand it reads
    an already-cached good set and passes without touching the
    verification, which is the exact no-op failure this test exists to
    catch. Without the clear afterwards it poisons every later test.
    """
    bad = tmp_path / "tampered.txt.gz"
    bad.write_bytes(gzip.compress(b"E119\tNot the real file\n"))

    vocab.load_icd10.cache_clear()
    monkeypatch.setattr(vocab, "ICD10_PATH", bad)
    try:
        with pytest.raises(ValueError, match="sha256"):
            vocab.load_icd10()
    finally:
        vocab.load_icd10.cache_clear()


def test_vendored_files_are_tracked_by_git():
    """The .gitignore rule is the one failure that passes locally and fails
    only in a fresh clone or in CI."""
    import shutil
    import subprocess
    if shutil.which("git") is None:
        pytest.skip("git binary not available")
    if not (vocab.REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    for path in (vocab.ICD10_PATH, vocab.HCPCS_PATH):
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            cwd=vocab.REPO_ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        assert result.returncode == 0, (
            f"{path.name} is not tracked by git. The .gitignore exception "
            f"in Task 2 is missing or placed above the data/* line."
        )


def test_known_real_codes_are_present():
    icd10 = vocab.load_icd10()
    assert vocab.normalize("E11.9") in icd10
    assert vocab.normalize("I10") in icd10
    hcpcs = vocab.load_hcpcs()
    assert vocab.normalize("J1885") in hcpcs
    assert vocab.normalize("G0008") in hcpcs


def test_hcpcs_loader_raises_on_tampered_content(tmp_path, monkeypatch):
    """Both loaders need this, not just one. A copy-paste bug such as
    _load(HCPCS_PATH, ICD10_SHA256) or reusing ICD10_PATH would otherwise
    ship undetected: the sha256 pin tests read the files directly rather
    than through the loaders, so they would still pass."""
    bad = tmp_path / "tampered.txt.gz"
    bad.write_bytes(gzip.compress(b"J1885\tNot the real file\n"))

    vocab.load_hcpcs.cache_clear()
    monkeypatch.setattr(vocab, "HCPCS_PATH", bad)
    try:
        with pytest.raises(ValueError, match="sha256"):
            vocab.load_hcpcs()
    finally:
        vocab.load_hcpcs.cache_clear()


def test_constants_are_filled_in_not_placeholders():
    """shared/vocab.py ships with literal <placeholder> markers that Task 1
    replaces. A leftover marker would otherwise reach a stored
    CodingOutput and silently destroy traceability."""
    assert vocab.VOCAB_VERSION
    assert "<" not in vocab.VOCAB_VERSION
    assert "<" not in vocab.ICD10_SHA256
    assert "<" not in vocab.HCPCS_SHA256
    assert vocab.ICD10_PATH.is_file()
    assert vocab.HCPCS_PATH.is_file()


def test_vocabulary_sizes_are_plausible():
    """Guards a parser that absorbs wrapped description lines as codes.
    A description continuing onto a second line would put its first token
    into the set, and a false member could mark a fabricated code
    verified. Loose bounds; this is a smoke test, not a pin."""
    assert 50_000 < len(vocab.load_icd10()) < 100_000
    assert 1_000 < len(vocab.load_hcpcs()) < 30_000
```

**Every hard-coded code above must be confirmed against the pinned releases
before you rely on it.** `M99` is a populated ICD-10-CM category, so a
plausible-looking fabrication can turn out to be real, and a test asserting
absence on a genuine code would pin the wrong behaviour while looking
correct.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_vocab.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'shared.vocab'`

- [ ] **Step 3: Write `shared/vocab.py`**

**Substitute the four placeholders as you paste this**: the two filenames,
the two sha256 pins, and `VOCAB_VERSION`, all from the `PROVENANCE.md` you
wrote in Task 1 Step 6. Pasted literally, the `<...>` markers make seven
tests fail. `test_constants_are_filled_in_not_placeholders` exists so that
failure is obvious rather than mysterious, but doing the substitution now
avoids it entirely.

```python
"""Vendored CMS code vocabularies and the one place codes are classified.

Two consumers depend on this module: services/agent_coding, and P2-4's
benchmark. The status mapping is the thing they must agree on, so classify()
is the only public entry point for it. A membership primitive is not
exposed, because two callers re-deriving a status from one is how the two
diverge silently and a scoring difference gets read as a model difference.

ICD-10-CM and HCPCS Level II are public domain and vendored. CPT is licensed
and is not, which is the only reason any code ends up "unchecked".
"""
from __future__ import annotations

import gzip
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
VOCAB_DIR = REPO_ROOT / "data" / "vocab"

# Filled in by Task 1 from the actual vendored artifacts.
ICD10_PATH = VOCAB_DIR / "icd10cm_codes_<FY>.txt.gz"
ICD10_SHA256 = "<pin from Task 1 step 5>"
HCPCS_PATH = VOCAB_DIR / "hcpcs_level2_<YEAR>.txt.gz"
HCPCS_SHA256 = "<pin from Task 1 step 5>"

# Names BOTH releases; recorded on every CodingOutput so a stored result is
# traceable to the exact pair that produced it. Bump this on ANY change to
# either pin. That is an obligation on this repo, not a property of the
# releases: ICD-10-CM moves annually, HCPCS Level II quarterly.
VOCAB_VERSION = "<from PROVENANCE.md>"

VocabularyStatus = Literal["verified", "not_found", "unchecked"]

# Five digits (99213), or four digits plus a trailing letter (0001T, 9999F)
# for Category II and III. Both lead with a digit, which is what makes this
# disjoint from ICD-10-CM and HCPCS Level II: those always lead with a
# letter. That disjointness is why a shape test is safe here and was not
# safe for HCPCS.
_CPT_RE = re.compile(r"^\d{5}$|^\d{4}[A-Z]$")


def normalize(code: str) -> str:
    """Lookup key: trimmed, uppercased, no decimal point.

    Load-bearing. CMS stores ICD-10-CM codes without the dot (E119) while
    models emit the dotted display form (E11.9). Without this every real
    ICD-10 code would read as a hallucination and the metric would be
    exactly inverted. HCPCS carries no decimal, so for it this is only the
    strip and uppercase, but both paths use one definition of a key.
    """
    return code.strip().upper().replace(".", "")


def _load(path: Path, expected_sha256: str) -> frozenset[str]:
    raw = gzip.decompress(path.read_bytes())
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"{path.name} failed its sha256 pin: expected {expected_sha256}, "
            f"got {actual}. The vocabulary changed without the pin being "
            f"updated, which would silently move any metric computed on it."
        )
    codes = set()
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        codes.add(normalize(line.split(None, 1)[0]))
    return frozenset(codes)


@lru_cache(maxsize=1)
def load_icd10() -> frozenset[str]:
    # Reads the module global at call time, NOT via a default argument. A
    # default binds at def time and would defeat the tamper test's
    # monkeypatch, turning it back into the no-op it exists to prevent.
    return _load(ICD10_PATH, ICD10_SHA256)


@lru_cache(maxsize=1)
def load_hcpcs() -> frozenset[str]:
    return _load(HCPCS_PATH, HCPCS_SHA256)


def _looks_like_cpt(code: str) -> bool:
    return bool(_CPT_RE.match(code))
```

Note there is deliberately no `_looks_like_hcpcs`. HCPCS Level II is a
letter plus four digits, and so are normalized ICD-10-CM codes (`M54.16`
becomes `M5416`). They are not separable by shape, which is why the real
HCPCS release is vendored instead of guessed at.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_vocab.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared/vocab.py tests/test_vocab.py
git commit -m "feat(P2-3): checksummed CMS vocabulary loaders"
```

---

### Task 4: `classify`

**Files:**
- Modify: `shared/vocab.py`
- Modify: `tests/test_vocab.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_classify_verifies_real_codes_regardless_of_declared_system():
    # Rules 1 and 2 ignore the label entirely.
    assert vocab.classify("ICD-10", "E11.9") == "verified"
    assert vocab.classify("CPT", "E11.9") == "verified"
    assert vocab.classify("HCPCS", "J1885") == "verified"
    assert vocab.classify("ICD-10", "J1885") == "verified"


def test_classify_unchecked_only_when_shape_and_label_both_say_cpt():
    assert vocab.classify("CPT", "99213") == "unchecked"
    assert vocab.classify("CPT", "0001T") == "unchecked"


def test_classify_fabricated_icd_shaped_code_declared_cpt_is_not_found():
    """The escape hatch. A model must not be able to exempt its own
    fabricated code by relabelling it."""
    assert vocab.classify("CPT", "M9999") == "not_found"


def test_classify_fabricated_icd_shaped_code_declared_hcpcs_is_not_found():
    """The specific hole an earlier draft opened with a shape-based HCPCS
    rule. M9999 is letter-plus-four-digits, exactly HCPCS shape."""
    assert vocab.classify("HCPCS", "M9999") == "not_found"


def test_classify_cpt_shaped_code_declared_hcpcs_is_not_found():
    """The other half of rule 3's label guard."""
    assert vocab.classify("HCPCS", "99213") == "not_found"


def test_classify_real_cpt_code_mislabelled_icd10_is_not_found():
    """A known, conservative distortion: a real code scored as a miss.
    Pinned so it stays deliberate rather than incidental."""
    assert vocab.classify("ICD-10", "99213") == "not_found"


def test_classify_unrecognised_system_still_gets_both_lookups():
    # Rules 1 and 2 ignore the label, so a real code verifies anyway.
    assert vocab.classify("LOINC", "E11.9") == "verified"
    # Only a code absent from BOTH sets is not_found here.
    assert vocab.classify("LOINC", "ZZZ999") == "not_found"


def test_classify_degenerate_input_is_not_found():
    assert vocab.classify("ICD-10", "") == "not_found"
    assert vocab.classify("ICD-10", "N/A") == "not_found"


def test_classify_is_normalization_insensitive():
    assert (vocab.classify("ICD-10", "e11.9")
            == vocab.classify("ICD-10", "E11.9")
            == vocab.classify("ICD-10", "E119")
            == "verified")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_vocab.py -k classify -v`
Expected: FAIL, `AttributeError: module 'shared.vocab' has no attribute 'classify'`

- [ ] **Step 3: Implement `classify`**

```python
def classify(system: str, code: str) -> VocabularyStatus:
    """Map a suggestion to a vocabulary status.

    Order is the design, not an implementation detail:

      1. ICD-10-CM lookup   -> verified, whatever the model called it
      2. HCPCS Level II     -> verified, whatever the model called it
      3. CPT shape AND declared CPT -> unchecked
      4. otherwise          -> not_found

    Rules 1 and 2 ignore the declared system on purpose. Routing on the
    model's own label would let it decide whether its code gets checked,
    so a model that drifts toward labelling things CPT would score better
    without hallucinating less. Rule 3 is the only place the label matters,
    and it is guarded by a shape test that cannot match either vendored
    system, since both of those always lead with a letter.

    `system` is a plain str rather than the schema Literal because P2-4 may
    call this outside the agent's validated path. An unrecognised value
    still gets both lookups; it just cannot satisfy rule 3.
    """
    key = normalize(code)
    if key in load_icd10():
        return "verified"
    if key in load_hcpcs():
        return "verified"
    if system == "CPT" and _looks_like_cpt(key):
        return "unchecked"
    return "not_found"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_vocab.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared/vocab.py tests/test_vocab.py
git commit -m "feat(P2-3): classify codes against vendored vocabularies"
```

---

### Task 5: `verified_rate`

Ships with zero in-repo callers. That is intentional, not dead code: its
consumer is P2-4, and the alternative is P2-4 re-deriving the rule and
diverging silently. Same reasoning as `classify`.

**Files:**
- Modify: `shared/vocab.py`
- Modify: `tests/test_vocab.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_verified_rate_pools_counts():
    assert vocab.verified_rate(9, 1) == 0.9


def test_verified_rate_is_none_not_zero_on_empty_denominator():
    """0.0 would report a perfect score for a run where nothing was
    checkable, which is the most misleading possible reading."""
    assert vocab.verified_rate(0, 0) is None


def test_pooled_rate_differs_from_mean_of_per_note_rates():
    """Documents the pooling rule from spec section 2a with a worked case.

    Note A: 1 verified, 0 not_found -> 1.0
    Note B: 1 verified, 3 not_found -> 0.25
    Mean of per-note rates = 0.625; pooled = 2/5 = 0.4.

    Honest about what this is: the counts are pre-summed before the call,
    so this documents the rule rather than testing that a caller pools.
    Nothing in this repo pools yet; P2-4 is the caller. The value is that
    the intended number is written down executably.
    """
    pooled = vocab.verified_rate(1 + 1, 0 + 3)
    mean_of_rates = (1.0 + 0.25) / 2
    assert pooled == 0.4
    assert pooled != mean_of_rates
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_vocab.py -k verified_rate -v`
Expected: FAIL, `AttributeError`

- [ ] **Step 3: Implement `verified_rate`**

```python
def verified_rate(verified: int, not_found: int) -> float | None:
    """Pooled verified rate. None (never 0.0) on an empty denominator.

    P2-4 sums verified_count and not_found_count across the held-out set
    and calls this ONCE. Averaging per-note rates is a different and worse
    number: it weights a note with one checkable code the same as a note
    with twelve, and it is undefined on the notes that need it least.

    "unchecked" is excluded from both sides, so this is the fraction of
    CHECKABLE codes present in the pinned releases. It is not a clean
    hallucination rate; see spec section 1a for the floor.
    """
    denominator = verified + not_found
    if denominator == 0:
        return None
    return verified / denominator
```

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/test_vocab.py -v && ruff check shared/vocab.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add shared/vocab.py tests/test_vocab.py
git commit -m "feat(P2-3): pooled verified-rate helper for P2-4"
```

---

## Chunk 2: Schema

### Task 6: Split the coding schema in two

**Files:**
- Modify: `shared/schemas.py:76-86`
- Modify: `tests/test_schemas.py:26`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_schemas.py`. **Merge the new names into the existing
`shared.schemas` import at lines 5 to 6 rather than adding a second import
statement**; `CodeSuggestion` is already imported there, and a duplicate is
F811, which `ruff check .` fails in CI.

```python
# extend the EXISTING import, do not add a new one
from shared.schemas import (
    CodeSuggestion, CodingOutput, ModelCodeSuggestion, ModelCodingPayload,
)


def test_model_payload_discards_a_claimed_vocabulary_status():
    """The trust boundary, tested at the schema level.

    The agent test pins observable behaviour, but it does so through
    pydantic's extra="ignore" default, which is config-sensitive. If a base
    config ever set extra="allow", the model would regain a channel to
    certify its own codes and the agent test might still pass.
    """
    suggestion = ModelCodeSuggestion(
        system="ICD-10", code="M9999", description="fabricated",
        vocabulary_status="verified",
    )
    assert not hasattr(suggestion, "vocabulary_status")


def test_model_payload_accepts_hcpcs_system():
    """Rejecting an honest HCPCS label would 502 on exactly the notes
    mentioning drugs and supplies, biasing the sample."""
    payload = ModelCodingPayload(
        codes=[ModelCodeSuggestion(
            system="HCPCS", code="J1885", description="Ketorolac injection")],
        confidence=0.8,
    )
    assert payload.codes[0].system == "HCPCS"


def test_counts_exclude_unchecked_from_both_sides():
    out = CodingOutput(
        codes=[
            CodeSuggestion(system="ICD-10", code="E11.9", description="a",
                           vocabulary_status="verified"),
            CodeSuggestion(system="ICD-10", code="M9999", description="b",
                           vocabulary_status="not_found"),
            CodeSuggestion(system="CPT", code="99213", description="c",
                           vocabulary_status="unchecked"),
        ],
        confidence=0.7, vocabulary_version="test-vocab",
    )
    assert out.verified_count == 1
    assert out.not_found_count == 1


def test_computed_counts_are_serialized_for_the_registry():
    """They must reach the agent_decisions output JSONB column."""
    out = CodingOutput(codes=[], confidence=0.5, vocabulary_version="v")
    dumped = out.model_dump()
    assert dumped["verified_count"] == 0
    assert dumped["not_found_count"] == 0
```

Update the existing bad-`system` case at `tests/test_schemas.py:26` to pass
`vocabulary_status` explicitly, so it keeps testing the rejection it was
written for rather than passing because a newly required field is missing.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_schemas.py -v`
Expected: FAIL, `ImportError: cannot import name 'ModelCodeSuggestion'`

- [ ] **Step 3: Replace `shared/schemas.py:76-86`**

```python
class ModelCodeSuggestion(BaseModel):
    """One code as the MODEL is allowed to state it.

    Deliberately carries no vocabulary_status. Model output is parsed into
    this, never into CodeSuggestion, so a model emitting
    vocabulary_status="verified" on a fabricated code has the key silently
    dropped by pydantic's extra="ignore" default. The model cannot certify
    its own hallucinations because it has no channel to make the claim.

    eligibility_flag means: this code is commonly subject to payer coverage
    or medical-necessity review, OR the note's documentation may not
    support it. Both are assessable from a note alone. Whether a specific
    patient's plan covers a service is NOT assessable here, and this agent,
    which receives no payer, plan, or benefits data, does not claim to
    answer it. When the flag is true, eligibility_reason is required; the
    agent degrades an unsubstantiated flag rather than rejecting the whole
    payload (see services/agent_coding/agent.py::_enrich).
    """
    system: Literal["ICD-10", "CPT", "HCPCS"]
    code: str
    description: str
    eligibility_flag: bool = False
    eligibility_reason: str | None = None


class ModelCodingPayload(BaseModel):
    """The whole model response, before the agent enriches it."""
    codes: list[ModelCodeSuggestion]
    confidence: float = Field(ge=0.0, le=1.0)


class CodeSuggestion(ModelCodeSuggestion):
    """One code as the AGENT returns it, with the status it computed."""
    vocabulary_status: Literal["verified", "not_found", "unchecked"]


class CodingOutput(BaseModel):
    agent_name: Literal["coding"] = "coding"
    codes: list[CodeSuggestion]
    confidence: float = Field(ge=0.0, le=1.0)
    # Names both vendored releases, so a stored result is traceable to the
    # vocabularies that produced it.
    vocabulary_version: str

    @computed_field
    @property
    def verified_count(self) -> int:
        return sum(1 for c in self.codes
                   if c.vocabulary_status == "verified")

    @computed_field
    @property
    def not_found_count(self) -> int:
        return sum(1 for c in self.codes
                   if c.vocabulary_status == "not_found")
```

Add `computed_field` to the pydantic import at the top of the file.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite to catch fallout**

Run: `pytest -q`
Expected: PASS. `CodeSuggestion` gained a required field, so anything
constructing one without it now fails. Fix any such call site.

- [ ] **Step 6: Commit**

```bash
git add shared/schemas.py tests/test_schemas.py
git commit -m "feat(P2-3): split coding schema so the model cannot set vocabulary status"
```

---

## Chunk 3: Agent and endpoint

### Task 7: `CodingError`, the parsing guards, and enrichment

Adopt the P2-1 structure rather than re-deriving it. The P2-1 spec (lines
130 to 133) explicitly deferred this here.

Parsing and enrichment land in one task on purpose. `run()` returns
`_enrich(payload)`, so a stubbed `_enrich` would leave this task's own
happy-path test failing and break the "every commit is green" property.
Everything `_enrich` needs (`classify` from Task 4, the schemas from Task 6)
already exists by now.

**Files:**
- Modify: `services/agent_coding/agent.py`
- Create: `tests/test_coding_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Mock target matters. agent.py does `from shared.llm import call`, which
binds the name into services.agent_coding.agent at import time. Patching
shared.llm.call has NO effect on the already-bound reference, so every
monkeypatch below targets services.agent_coding.agent.* specifically. This
is the same trap documented in the P2-1 spec.
"""
import json

import pytest

from services.agent_coding import agent as coding_agent
from shared import vocab
from shared.llm import TruncatedResponseError
from shared.schemas import AgentInput, SoapNote

SOAP = SoapNote(subjective="s", objective="o", assessment="a", plan="p")
INPUT = AgentInput(encounter_id=1, note_id=1, soap=SOAP)


def _patch(monkeypatch, response):
    """Returns a dict that captures the log_decision kwargs."""
    def fake_call(component, system, user, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(coding_agent, "call", fake_call)
    logged = {}
    monkeypatch.setattr(coding_agent, "log_decision",
                        lambda **kw: logged.update(kw))
    return logged


def _one(system, code, **kw):
    entry = {"system": system, "code": code, "description": "d", **kw}
    return json.dumps({"codes": [entry], "confidence": 0.8})


# ---------- parsing guards ----------

def test_malformed_json_raises_coding_error(monkeypatch):
    _patch(monkeypatch, "not json at all")
    with pytest.raises(coding_agent.CodingError):
        coding_agent.run(INPUT)


def test_json_array_instead_of_object_raises_coding_error(monkeypatch):
    """A bare array raises TypeError from **data, which neither the
    MalformedJSONError nor the ValidationError handler catches. This is the
    guard that is easy to leave out."""
    _patch(monkeypatch, '[{"system": "ICD-10"}]')
    with pytest.raises(coding_agent.CodingError):
        coding_agent.run(INPUT)


def test_confidence_out_of_range_raises_coding_error(monkeypatch):
    _patch(monkeypatch, '{"codes": [], "confidence": 1.5}')
    with pytest.raises(coding_agent.CodingError):
        coding_agent.run(INPUT)


def test_error_preview_is_truncated(monkeypatch):
    _patch(monkeypatch, "x" * 5000)
    with pytest.raises(coding_agent.CodingError) as exc:
        coding_agent.run(INPUT)
    assert len(str(exc.value)) < 500


def test_truncated_response_becomes_coding_error(monkeypatch):
    """TruncatedResponseError is a RuntimeError, so without an explicit
    catch it bypasses CodingError and app.py returns 500 instead of 502.
    This agent emits the largest output of the three, so it is the most
    likely to hit the cap."""
    _patch(monkeypatch, TruncatedResponseError("coding", 1500))
    with pytest.raises(coding_agent.CodingError):
        coding_agent.run(INPUT)


# ---------- happy path and registry logging ----------

def test_empty_codes_list_round_trips(monkeypatch):
    _patch(monkeypatch, '{"codes": [], "confidence": 0.5}')
    out = coding_agent.run(INPUT)
    assert out.codes == []


def test_happy_path_logs_the_decision(monkeypatch):
    """Every agent logging to the registry is a CLAUDE.md convention, and
    P2-7 depends on it. tests/test_prior_auth_agent.py asserts the same
    fields."""
    logged = _patch(monkeypatch, _one("ICD-10", "E11.9"))
    out = coding_agent.run(INPUT)
    assert logged["encounter_id"] == 1
    assert logged["note_id"] == 1
    assert logged["agent_name"] == "coding"
    assert logged["confidence"] == out.confidence
    assert logged["output"] == out.model_dump()
    assert logged["model"] and logged["effort"]
    assert isinstance(logged["latency_ms"], int)


# ---------- enrichment: the agent computes status, the model cannot ----------

def test_real_icd10_code_is_verified(monkeypatch):
    _patch(monkeypatch, _one("ICD-10", "E11.9"))
    assert coding_agent.run(INPUT).codes[0].vocabulary_status == "verified"


def test_fabricated_code_is_not_found_and_still_returned(monkeypatch):
    _patch(monkeypatch, _one("ICD-10", "M9999"))
    out = coding_agent.run(INPUT)
    assert out.codes[0].vocabulary_status == "not_found"
    assert out.codes[0].code == "M9999"   # kept, not dropped
    assert out.not_found_count == 1


def test_cpt_code_is_unchecked_never_not_found(monkeypatch):
    _patch(monkeypatch, _one("CPT", "99213"))
    out = coding_agent.run(INPUT)
    assert out.codes[0].vocabulary_status == "unchecked"
    assert out.not_found_count == 0
    assert out.verified_count == 0


def test_hcpcs_suggestion_flows_end_to_end(monkeypatch):
    """Regression test for the 502-and-biased-sample failure that admitting
    "HCPCS" exists to prevent. Nothing else at agent level touches the
    third system value."""
    _patch(monkeypatch, _one("HCPCS", "J1885"))
    out = coding_agent.run(INPUT)
    assert out.codes[0].vocabulary_status == "verified"


def test_fabricated_code_mislabelled_cpt_is_still_not_found(monkeypatch):
    """The escape hatch, tested through the agent rather than only through
    classify. This pins that _enrich passes system through unbranched
    instead of deciding for itself."""
    _patch(monkeypatch, _one("CPT", "M9999"))
    assert coding_agent.run(INPUT).codes[0].vocabulary_status == "not_found"


def test_real_icd10_code_mislabelled_cpt_is_still_verified(monkeypatch):
    _patch(monkeypatch, _one("CPT", "E11.9"))
    assert coding_agent.run(INPUT).codes[0].vocabulary_status == "verified"


def test_real_cpt_code_mislabelled_icd10_is_not_found(monkeypatch):
    """The conservative distortion the spec admits in section 1a."""
    _patch(monkeypatch, _one("ICD-10", "99213"))
    assert coding_agent.run(INPUT).codes[0].vocabulary_status == "not_found"


def test_model_claim_of_verified_is_overridden(monkeypatch):
    """THE most important test in this file. Without it the trust boundary
    is unenforced."""
    _patch(monkeypatch, _one("ICD-10", "M9999",
                             vocabulary_status="verified"))
    assert coding_agent.run(INPUT).codes[0].vocabulary_status == "not_found"


def test_mixed_response_counts_exclude_unchecked(monkeypatch):
    _patch(monkeypatch, json.dumps({"codes": [
        {"system": "ICD-10", "code": "E11.9", "description": "d"},
        {"system": "ICD-10", "code": "M9999", "description": "d"},
        {"system": "CPT", "code": "99213", "description": "d"},
    ], "confidence": 0.8}))
    out = coding_agent.run(INPUT)
    assert out.verified_count == 1
    assert out.not_found_count == 1
    assert len(out.codes) == 3


def test_vocabulary_version_comes_from_the_module(monkeypatch):
    _patch(monkeypatch, _one("ICD-10", "E11.9"))
    assert coding_agent.run(INPUT).vocabulary_version == vocab.VOCAB_VERSION


def test_model_cannot_set_vocabulary_version(monkeypatch):
    _patch(monkeypatch, json.dumps({
        "codes": [], "confidence": 0.5, "vocabulary_version": "attacker"}))
    assert coding_agent.run(INPUT).vocabulary_version == vocab.VOCAB_VERSION
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_coding_agent.py -v`
Expected: FAIL, `AttributeError: module ... has no attribute 'CodingError'`

- [ ] **Step 3: Rewrite `services/agent_coding/agent.py`**

This block replaces everything in the file **except** `_SYSTEM`. Leave the
existing `_SYSTEM` string exactly where it is for now; Task 9 rewrites it.
Pasting this block without keeping `_SYSTEM` gives `NameError` at the
`call()` line.

```python
"""Coding/Eligibility Agent: suggest codes, flag possible eligibility issues.

Codes are SUGGESTIONS for human review, never confirmed codes. Whether a
code exists is decided here by lookup, not by the model: see shared/vocab.py.
"""
from __future__ import annotations

import time

from pydantic import ValidationError

from shared import vocab
from shared.llm import (
    MalformedJSONError, ROUTING, TruncatedResponseError, call, extract_json,
)
from shared.registry import log_decision
from shared.schemas import (
    AgentInput, CodeSuggestion, CodingOutput, ModelCodingPayload,
)

# Provisional. Task 13 pins the real value from observed usage. The library
# default is 1500 and this agent emits the largest output of the three, so
# the cap is a live truncation risk rather than a formality.
_MAX_TOKENS = 4000


class CodingError(ValueError):
    """Raised when the model's response cannot be parsed into a CodingOutput.

    Carries a truncated preview of the raw output so a failure is
    diagnosable without dumping full model output into logs.
    """

    def __init__(self, reason: str, raw: str):
        preview = raw[:200] + ("..." if len(raw) > 200 else "")
        super().__init__(
            f"Coding parsing failed: {reason}. Raw output: {preview!r}")


def _enrich(payload: ModelCodingPayload) -> CodingOutput:
    """Turn what the model said into what the agent stands behind.

    The agent never branches on `system` itself; that decision lives in
    vocab.classify, so this file and P2-4 cannot drift apart.
    """
    codes = []
    for suggestion in payload.codes:
        # Stored in conventional dotted display form. Only the LOOKUP is
        # normalized, and classify does that internally. Do not pass this
        # through vocab.normalize: it strips the dot and would destroy the
        # display form this line exists to preserve.
        code = suggestion.code.strip().upper()
        codes.append(CodeSuggestion(
            system=suggestion.system,
            code=code,
            description=suggestion.description,
            eligibility_flag=suggestion.eligibility_flag,
            eligibility_reason=suggestion.eligibility_reason,
            vocabulary_status=vocab.classify(suggestion.system, code),
        ))
    return CodingOutput(
        codes=codes,
        confidence=payload.confidence,
        vocabulary_version=vocab.VOCAB_VERSION,
    )


def run(inp: AgentInput) -> CodingOutput:
    model, effort = ROUTING["coding"]
    started = time.perf_counter()

    try:
        raw = call("coding", system=_SYSTEM, user=inp.soap.model_dump_json(),
                   max_tokens=_MAX_TOKENS)
    except TruncatedResponseError as exc:
        # raw="" because call() raises before returning any text. There is
        # no response to preview; the reason carries the diagnosis.
        raise CodingError(str(exc), "") from exc

    try:
        data = extract_json(raw)
    except MalformedJSONError as exc:
        raise CodingError(exc.reason, raw) from exc

    if not isinstance(data, dict):
        raise CodingError("JSON was not an object", raw)

    try:
        payload = ModelCodingPayload(**data)
    except ValidationError as exc:
        raise CodingError(
            f"did not match the ModelCodingPayload schema ({exc})",
            raw) from exc

    out = _enrich(payload)

    latency_ms = int((time.perf_counter() - started) * 1000)
    log_decision(
        encounter_id=inp.encounter_id, note_id=inp.note_id,
        agent_name="coding", model=model, effort=effort,
        input_ref=inp.soap.model_dump(), output=out.model_dump(),
        confidence=out.confidence, latency_ms=latency_ms,
    )
    return out
```

No retry loop on malformed JSON. P1-2's retry was justified by a measured
rate (1 malformed sample in a 120-note run) and no equivalent measurement
exists for this agent yet.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_coding_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/agent_coding/agent.py tests/test_coding_agent.py
git commit -m "feat(P2-3): agent computes vocabulary status, model cannot claim it"
```

---

### Task 8: Degrade unsubstantiated eligibility flags

**Files:**
- Modify: `services/agent_coding/agent.py`
- Modify: `tests/test_coding_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_unsubstantiated_eligibility_flag_degrades_without_losing_others(
        monkeypatch):
    """The second half is the point: it is the regression test against
    reintroducing whole-output rejection and its sampling bias. A
    model_validator would have discarded the good code alongside the bad
    flag, and the loss would have looked like an ordinary parse failure."""
    _patch(monkeypatch, json.dumps({"codes": [
        {"system": "ICD-10", "code": "E11.9", "description": "d",
         "eligibility_flag": True, "eligibility_reason": "   "},
        {"system": "ICD-10", "code": "I10", "description": "d"},
    ], "confidence": 0.8}))
    out = coding_agent.run(INPUT)
    assert out.codes[0].eligibility_flag is False
    assert len(out.codes) == 2
    assert out.codes[1].vocabulary_status == "verified"


def test_eligibility_flag_with_null_reason_degrades(monkeypatch):
    _patch(monkeypatch, _one("ICD-10", "E11.9", eligibility_flag=True))
    assert coding_agent.run(INPUT).codes[0].eligibility_flag is False


def test_substantiated_eligibility_flag_is_preserved(monkeypatch):
    _patch(monkeypatch, _one("ICD-10", "E11.9", eligibility_flag=True,
                             eligibility_reason="Commonly requires review"))
    assert coding_agent.run(INPUT).codes[0].eligibility_flag is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_coding_agent.py -k eligibility -v`
Expected: FAIL on the first two. `_enrich` currently passes the flag
through unchanged.

- [ ] **Step 3: Add the degradation to `_enrich`**

Inside the existing loop, between the `code = ...` line and the
`codes.append(...)` call:

```python
        flag = suggestion.eligibility_flag
        reason = suggestion.eligibility_reason
        if flag and not (reason or "").strip():
            # Degrade THIS suggestion only. Rejecting the whole payload
            # would discard every correctly validated code alongside it and
            # bias any rate computed over successful runs, while looking
            # from the outside like an ordinary parse failure.
            flag = False
            reason = None   # do not leave a blank reason on a cleared flag
```

Then change the `codes.append(...)` call to pass `eligibility_flag=flag`
and `eligibility_reason=reason` instead of reading them off `suggestion`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_coding_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/agent_coding/agent.py tests/test_coding_agent.py
git commit -m "feat(P2-3): degrade unsubstantiated eligibility flags per suggestion"
```

---

### Task 9: Rewrite the system prompt

The current `_SYSTEM` advertises the old shape with no reason field, so
leaving it would make Task 8's degradation path fire constantly on flags the
model was never asked to justify.

**Files:**
- Modify: `services/agent_coding/agent.py`

- [ ] **Step 1: Replace `_SYSTEM`**

```python
_SYSTEM = (
    "You suggest likely ICD-10, CPT, and HCPCS Level II codes for a SOAP "
    "note and flag codes that may face coverage review. Return only JSON: "
    '{"codes": [{"system": "ICD-10", "code": "", "description": "", '
    '"eligibility_flag": false, "eligibility_reason": null}], '
    '"confidence": 0.0}. '
    "system must be exactly one of \"ICD-10\", \"CPT\", or \"HCPCS\". "
    "Write ICD-10 codes in conventional dotted form, for example E11.9. "
    "Set eligibility_flag true only when the code is commonly subject to "
    "payer coverage or medical-necessity review, or the note's "
    "documentation may not support it; when you do, eligibility_reason is "
    "REQUIRED and must say why. You cannot determine whether a specific "
    "patient's plan covers a service, and must not claim to. "
    "These are suggestions for human review, not confirmed codes. "
    "Confidence is your calibrated certainty in [0, 1]."
)
```

Say nothing about `vocabulary_status` or `vocabulary_version`. Those are not
the model's to supply and mentioning them invites an attempt.

- [ ] **Step 2: Run the suite**

Run: `pytest tests/test_coding_agent.py -v && ruff check services/agent_coding/`
Expected: PASS, no lint findings

- [ ] **Step 3: Commit**

```bash
git add services/agent_coding/agent.py
git commit -m "feat(P2-3): system prompt matches the model payload schema"
```

---

### Task 10: Endpoint returns 502 on `CodingError`

**Files:**
- Modify: `services/agent_coding/app.py`
- Create: `tests/test_coding_app.py`

- [ ] **Step 1: Write the failing tests**

```python
from fastapi.testclient import TestClient
from services.agent_coding import app as coding_app
from services.agent_coding.agent import CodingError

client = TestClient(coding_app.app)
BODY = {"encounter_id": 1, "note_id": 1, "soap": {
    "subjective": "s", "objective": "o", "assessment": "a", "plan": "p"}}


def test_health():
    resp = client.get("/health")
    assert resp.json() == {"status": "ok", "service": "agent_coding"}


def test_run_happy_path(monkeypatch):
    from shared.schemas import CodingOutput
    monkeypatch.setattr(coding_app, "run", lambda inp: CodingOutput(
        codes=[], confidence=0.5, vocabulary_version="v"))
    resp = client.post("/run", json=BODY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_name"] == "coding"
    assert body["codes"] == []
    assert body["vocabulary_version"] == "v"
    # Computed fields must survive FastAPI's response_model serialization,
    # or the registry and P2-4 lose the counts.
    assert body["verified_count"] == 0
    assert body["not_found_count"] == 0


def test_run_returns_502_on_coding_error(monkeypatch):
    """P2-6 needs a clean per-agent failure signal to isolate one agent's
    failure from the other two."""
    def boom(inp):
        raise CodingError("model broke", "raw")
    monkeypatch.setattr(coding_app, "run", boom)
    resp = client.post("/run", json=BODY)
    assert resp.status_code == 502
    assert "model broke" in resp.json()["detail"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_coding_app.py -v`
Expected: FAIL on the 502 case (currently an unhandled 500)

- [ ] **Step 3: Update `services/agent_coding/app.py`**

```python
from fastapi import FastAPI, HTTPException
from shared.schemas import AgentInput, CodingOutput
from services.agent_coding.agent import CodingError, run

app = FastAPI(title="Care Ops Copilot - Coding Agent")


@app.get("/health")
def health():
    return {"status": "ok", "service": "agent_coding"}


@app.post("/run", response_model=CodingOutput)
def run_endpoint(inp: AgentInput):
    try:
        return run(inp)
    except CodingError as exc:
        # 502: the model or the pipeline broke, not this service.
        raise HTTPException(502, str(exc)) from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_coding_app.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/agent_coding/app.py tests/test_coding_app.py
git commit -m "feat(P2-3): coding endpoint returns 502 on CodingError"
```

---

## Chunk 4: Container, docs, and live verification

### Task 11: Ship the vocabulary into the image

Without this the loaders raise on the first request in every container and
`/run` fails always. The symptom would otherwise surface during P2-5 as a
readiness-probe failure with nothing pointing back here.

**Files:**
- Modify: `services/agent_coding/Dockerfile`

- [ ] **Step 1: Add the COPY line after the `shared/` copy**

```dockerfile
COPY data/vocab/ data/vocab/
```

- [ ] **Step 2: Verify the image builds and the vocabulary loads**

```bash
docker build -f services/agent_coding/Dockerfile -t care-ops-agent_coding .
docker run --rm care-ops-agent_coding python -c \
  "from shared import vocab; print(vocab.VOCAB_VERSION, len(vocab.load_icd10()), len(vocab.load_hcpcs()))"
```
Expected: the version string and two non-zero counts. A `FileNotFoundError`
here means the COPY landed in the wrong place.

- [ ] **Step 3: Commit**

```bash
git add services/agent_coding/Dockerfile
git commit -m "fix(P2-3): ship vendored vocabularies into the coding image"
```

---

### Task 12: Update `docs/TECH-DESIGN.md`

**Files:**
- Modify: `docs/TECH-DESIGN.md:114`

- [ ] **Step 1: Replace the stale `CodingOutput` JSON block at lines 113-115**

Line 114 is only the middle line; the block body is 113 to 115, with the
code fences on 112 and 116.

```json
{
  "agent_name": "coding",
  "codes": [
    {
      "system": "ICD-10",
      "code": "E11.9",
      "description": "Type 2 diabetes mellitus without complications",
      "vocabulary_status": "verified",
      "eligibility_flag": false,
      "eligibility_reason": null
    }
  ],
  "confidence": 0.0,
  "vocabulary_version": "ICD-10-CM FY2026 + HCPCS Level II 2026",
  "verified_count": 1,
  "not_found_count": 0
}
```

- [ ] **Step 2: Add a sentence under the block**

State that `system` accepts `"ICD-10"`, `"CPT"`, or `"HCPCS"`; that
`vocabulary_status` is computed by the agent and is one of `verified`,
`not_found`, or `unchecked`; and that `verified_count` and
`not_found_count` are computed fields excluding `unchecked` from both, per
the P2-3 spec's section 2a.

Use the real `VOCAB_VERSION` string from Task 1 rather than the placeholder
above.

- [ ] **Step 3: Commit**

```bash
git add docs/TECH-DESIGN.md
git commit -m "docs(P2-3): update CodingOutput shape in tech design"
```

---

### Task 13: Live verification against the real API

Manual, not an automated test. Follows the P1-3 and P2-1 precedent. This is
the step that closes P2-3's roadmap exit criteria.

- [ ] **Step 1: Bring up Postgres and confirm the environment**

`run()` calls `log_decision`, which inserts into `agent_decisions`. That
table has `NOT NULL REFERENCES` to both `encounters(id)` and `notes(id)`
(see `db/schema.sql`), and `make db-init` seeds neither. A hardcoded
`encounter_id` would hit the foreign-key constraint the moment `log_decision`
runs. The script below uses `insert_encounter`/`insert_note` from
`shared/db.py`, the same helpers `services/intake/app.py` uses. P2-1 hit
this exact wall; do not shortcut it.

```bash
docker compose up -d db
make db-init
```

Confirm `.env` has a real `ANTHROPIC_API_KEY`. Do **not** `source .env`;
values can execute as commands. The app reads it through
`shared/config.py`.

- [ ] **Step 2: Run two real SOAP notes and capture token usage**

The cap is deliberately generous here so the observation is not itself
truncated. `shared/llm.py::call` discards the response object, so
`resp.usage` never reaches the caller; the wrapper below is how the counts
become observable without changing library code.

```bash
python - <<'EOF'
import shared.llm as llm
from shared.db import insert_encounter, insert_note
from services.agent_coding import agent as coding_agent
from shared.schemas import AgentInput, SoapNote

# Observe output tokens without editing shared/llm.py. call() resolves
# _client at call time, so wrapping the bound method here is enough.
_real_create = llm._client.messages.create
observed = []


def spy(**kwargs):
    resp = _real_create(**kwargs)
    observed.append(resp.usage.output_tokens)
    return resp


llm._client.messages.create = spy

rich = SoapNote(
    subjective="55-year-old with type 2 diabetes here for follow-up, also "
               "reports burning feet at night and a productive cough.",
    objective="BP 148/92. A1c 8.4. Diminished monofilament sensation "
              "bilaterally. Coarse crackles at the right base.",
    assessment="Type 2 diabetes, uncontrolled, with peripheral neuropathy. "
               "Hypertension. Community-acquired pneumonia.",
    plan="Increase metformin. Start lisinopril. Ketorolac given in office "
         "for pain. Chest X-ray obtained. Follow up in 2 weeks.",
)
simple = SoapNote(
    subjective="Here for a routine wellness check, feeling well.",
    objective="Vitals normal, exam unremarkable.",
    assessment="Healthy adult, no acute issues.",
    plan="Routine follow-up in 12 months.",
)

try:
    for label, soap in [("RICH", rich), ("SIMPLE", simple)]:
        encounter_id = insert_encounter(None, "transcript")
        note_id = insert_note(encounter_id, soap.model_dump(),
                              "manual-verification", "xhigh")
        inp = AgentInput(encounter_id=encounter_id, note_id=note_id,
                         soap=soap)
        print(f"=== {label} ===")
        try:
            print(coding_agent.run(inp).model_dump_json(indent=2))
        except Exception as exc:            # noqa: BLE001
            print(f"FAILED: {type(exc).__name__}: {exc}")
finally:
    # In a finally block so a failing note does not cost the observation
    # that Step 5 pins max_tokens from.
    print(f"=== OUTPUT TOKENS: {observed} ===")
    if observed:
        print(f"=== MAX OBSERVED: {max(observed)} ===")
EOF
```

- [ ] **Step 3: Confirm the lookup path works on real model output**

At least one suggested code must come back `verified`. **If every code is
`not_found`, suspect the normalization or the parser before suspecting the
model**, since that is the exact symptom of a dot-handling bug or of having
vendored the `order` file instead of the `codes` file.

The rich note is written to elicit an HCPCS-eligible item (the ketorolac)
and a procedure, so it should exercise more than one `system` value.

- [ ] **Step 4: Classify every `not_found` code by cause**

Use the four causes in spec section 1a: fabricated, real but absent from
the pinned releases, a real CPT code the model mislabelled, or degenerate
input. **This is the only measurement of the metric's floor that this task
produces.** Without it, P2-4 cannot tell a model that hallucinates from one
that simply predates the pinned release. Record the tally in the PR
description.

- [ ] **Step 5: Pin `_MAX_TOKENS` from the observed usage**

Take `max(observed)` from Step 2, double it, round up to the nearest 500,
and use at least 2000. Replace the provisional `_MAX_TOKENS = 4000` in
`services/agent_coding/agent.py` with that number and change the comment
above it to record the observation it came from, for example
"largest observed output was 1,180 tokens over two live notes".

Worth choosing carefully now, because the protection you would expect is
not there: `governance/llm_cache.py::cache_key` takes
`(task, model, prompt_version, payload)` and does **not** include
`max_tokens`. Only `governance/structuring_eval.py:82` folds it in, by
convention; `facts.py` and `judge.py` pass a bare `"v1"`. So when P2-4 adds
caching for coding calls, its `prompt_version` must include `max_tokens`
and the prompt hash, following the `structuring_eval.py` pattern. Otherwise
changing the cap mid-benchmark produces silent cache **hits** that blend two
configurations into one number.

- [ ] **Step 6: Capture raw request and response JSON in the PR description**

Not committed as a fixture. This is the manual sign-off that closes P2-3's
exit criteria, the same way P1-3 was first verified by hand.

- [ ] **Step 7: Final check and commit**

```bash
pytest -q && ruff check .
git add services/agent_coding/agent.py
git commit -m "feat(P2-3): pin max_tokens from observed live usage"
```

---

## Done when

- [ ] `make test` green with the new test files, and `ruff check .` clean
- [ ] A SOAP note yields a valid `CodingOutput` (roadmap criterion 1)
- [ ] Codes carry `vocabulary_status` and are presented as suggestions
      (roadmap criterion 2)
- [ ] `eligibility_flag` is a structured boolean with a required reason
      (roadmap criterion 3)
- [ ] Live verification captured in the PR, including the `not_found` cause
      tally
- [ ] A PR shows a green CI run, per the P1-6 pattern now established
