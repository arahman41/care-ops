# P2-4 Coding Configuration Routing Benchmark Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pre-registered, replayable benchmark that decides what `shared/llm.py::ROUTING["coding"]` should hold, comparing the configuration Sonnet 5 at xhigh against Opus 4.8 at high on the 120-note ACI-Bench held-out set, without ever calling the result "coding accuracy" or attributing it to a model.

**Architecture:** A new routing seam in `shared/llm.py` (`call_detailed()` returning an `LLMResult`) lets the benchmark issue per-arm `(model, effort)` overrides and capture `resp.model` and token counts, which `call()` discards today. The benchmark reuses `services/agent_coding` parsing and `shared/vocab.classify`, dedups per note, computes rates as pooled ratios-of-sums in percentage points, intervals via a hand-rolled note-level paired BCa bootstrap, and applies a literal pre-registered decision rule with four guards. Every rate recomputes offline from stored per-code verdicts. Cost/latency are measured once and verified, not recomputed. The full run and the routing write are human-gated.

**Tech Stack:** Python 3.12, pydantic 2.10, anthropic 0.116, numpy 2.0 + scipy 1.15 (already present transitively via scikit-learn; pinned explicitly here), psycopg 3, pytest 8.

---

## Read before starting

The design this plan implements is `docs/superpowers/specs/2026-07-19-p2-4-coding-routing-benchmark-design.md` (approved, review round 5). **Read it in full first.** This plan is the how; that spec is the why, and every non-obvious choice below traces to a numbered section there. Where this plan says "spec §N", open §N.

The single most dangerous property of this task: **a subtle arithmetic or scale bug produces a plausible number that silently misroutes production and misreports a benchmark.** That is why the session runs Opus 4.8 at max (`docs/MODEL-EFFORT-GUIDE.md`, P2-4). Prefer a loud failure to a plausible one, everywhere.

### Non-negotiable invariants (violating any one invalidates the benchmark)

1. **Never attribute a result to a model.** The unit under test is a `(model, effort)` configuration. "Configuration A beat configuration B" is the only licensed phrasing (spec §1).
2. **Never call any number "coding accuracy."** Neither held-out set carries gold codes. The verified rate says a code *exists in the pinned CMS release*, never that it is *right for the note* (spec §1).
3. **All rates, thresholds, and interval endpoints are percentage points on a 0–100 scale.** `vocab.verified_rate` returns a proportion; multiply by 100 at the boundary. `delta = 1.5` points is a pre-registered constant, never recomputed (spec §2, §4).
4. **Dedup on `normalize(code)` alone, per note.** Not `(system, code)`. Not global. Recompute counts from deduped verdicts; `CodingOutput.verified_count`/`not_found_count` are forbidden here (spec §1).
5. **The benchmark calls `call_detailed()` + `parse_and_enrich`, never `agent.run()`** (spec §7).
6. **A missing price table is a terminal state:** `ROUTING["coding"]` is left unchanged and no winner is named (spec §2, §8).

### Cost and money gates

A cold full run is 240 real API calls (120 notes × 2 arms) at xhigh and high effort. Chunks 1–6 build and unit-test the machinery with fakes and stubs and spend nothing. Chunk 7 contains the only steps that spend money or touch routing, and every one of them is explicitly human-gated. Do not run the pilot or the full benchmark as part of ordinary task execution.

### Setup already true in this environment (do not redo)

- Branch is `p2-4-coding-benchmark`; the spec is committed on it.
- `data/vocab/` holds the pinned ICD-10-CM FY2026 (74,719 codes) and HCPCS Level II 2026Q3 (8,725) gzips. `data/aci-bench/data/challenge_data/*.csv` and `scripts/heldout_split.lock.json` are present.
- The venv is at `.venv/`; activate with `source .venv/Scripts/activate` (Git Bash) before running python.
- `numpy 2.0.2` and `scipy 1.15.3` import today.
- These counts were re-measured on 2026-07-22 and match the spec: held-out ACI n=120, empty-plan stratum=27, non-empty=93, ACI train pool=67.

---

## File structure

**New modules (all pure/testable without a DB or API key except where noted):**

- `governance/coding_metrics.py` — per-note dedup with pinned conflict resolution, denominators, and the arm-specific floor band (causes 1–4, cause-3 upper bound, `_CODE_SHAPE_RE`/`_PLACEHOLDERS`). Pure. (spec §1, §5)
- `governance/coding_bootstrap.py` — note-level paired bootstrap with shared indices and hand-rolled BCa (mid-rank `z0`, jackknife acceleration, `None`-denominator dropping). Pure numerical. (spec §4)
- `governance/coding_decision.py` — the literal pre-registered decision rule and the four guards. Pure. (spec §2)
- `governance/pricing.py` — load and validate `governance/pricing.json`; compute per-arm cost; absent table is a typed "no cost winner" state, not a crash. (spec §8)
- `governance/coding_benchmark.py` — orchestration: input construction from ACI reference notes, stratification on `plan == ""`, per-arm execution via `call_detailed` + `parse_and_enrich`, `LLMResult` caching, intersection/failure policy, artifact + roster construction, `record_coding_run` wiring, replay. Touches API + DB at run time only. (spec §3, §6, §8)
- `governance/coding_pilot.py` — a small ACI **train** loader (spec forbids a train path in `heldout.py`) and the pilot report (`rho`, design effect, guard statistics, `v`, equivalence-attainable). (spec §8)
- `scripts/run_coding_benchmark.py` — CLI: `--pilot`, full run, `--replay`. Modeled on `scripts/run_structuring_eval.py`. (spec §6, §8)
- `scripts/measure_fy2025_diff.py` — one-off: measure `|FY2025 \ FY2026|` to decide the cause-2 vendoring (spec §5a).

**Modified:**

- `shared/llm.py` — add `LLMResult`, `_UNSET`, `call_detailed()`; make `call()` a thin wrapper. No existing caller changes. (spec §7)
- `services/agent_coding/agent.py` — extract `parse_and_enrich(raw) -> CodingOutput`; `run()` calls it. (spec §7)
- `governance/evaluate.py` — add `record_coding_run(...)`; leave `record_structuring_run` untouched. (spec §6)
- `db/schema.sql` — idempotent `ALTER TABLE eval_runs ADD COLUMN ... metrics JSONB`, `model_effort TEXT`. (spec §6)
- `governance/requirements.txt` — add explicit `numpy` and `scipy` pins (now a direct dependency).
- `Makefile` — `coding-benchmark`, `coding-benchmark-pilot`, `coding-benchmark-replay`, `coding-fy2025-diff` targets.
- `docs/ROADMAP.md` — P2-4 progress/gate notes.
- `docs/MODEL-EFFORT-GUIDE.md` — Layer B: record the winning configuration (terminal, human-gated).
- `data/vocab/PROVENANCE.md` — only if FY2025 clears the §5a gate.

**New tests:** `tests/test_llm_call_detailed.py`, `tests/test_coding_metrics.py`, `tests/test_coding_bootstrap.py`, `tests/test_coding_decision.py`, `tests/test_pricing.py`, `tests/test_coding_benchmark.py`, `tests/test_coding_pilot.py`. Extend `tests/test_coding_agent.py` for `parse_and_enrich`.

**Gitignore:** the roster is written as a `.full.json` sibling under `governance/eval_artifacts/`, which the existing `governance/eval_artifacts/*.full.json` rule already excludes. A step verifies this rather than assuming it.

---

## Chunk 1: The routing seam

Everything downstream needs per-arm `(model, effort)` overrides and `resp.model` + token counts, which `call()` discards. This chunk adds `call_detailed()` and extracts one parsing path. It is the bug an earlier draft would have shipped (spec §7): adding overrides to `agent.run()` alone would change the logged label, not the API call, so both arms would issue identical requests while the artifact named one Opus.

**Files:**
- Modify: `shared/llm.py`
- Modify: `services/agent_coding/agent.py`
- Test: `tests/test_llm_call_detailed.py` (create)
- Test: `tests/test_coding_agent.py` (extend)

### Task 1.1: `LLMResult` and the `_UNSET` sentinel

- [ ] **Step 1: Write the failing test** in `tests/test_llm_call_detailed.py`

```python
"""P2-4 routing seam: call_detailed() exposes what call() discards, and
carries per-arm (model, effort) overrides all the way to the outbound request.

Mock target: shared.llm._client.messages.create. call_detailed builds kwargs
and calls the client, so patching the client captures the real outbound request.
This is the test that a naive 'override in run()' implementation fails.
"""
from __future__ import annotations

import pytest

import shared.llm as llm
from shared.llm import LLMResult, _UNSET, call, call_detailed


class _Resp:
    """Minimal stand-in for an anthropic Message."""
    def __init__(self, text="ok", model="claude-sonnet-5-20260101",
                 stop_reason="end_turn", input_tokens=11, output_tokens=22):
        self.stop_reason = stop_reason
        self.model = model
        self.content = [type("Block", (), {"type": "text", "text": text})()]
        self.usage = type("Usage", (), {"input_tokens": input_tokens,
                                        "output_tokens": output_tokens})()


def _capture(monkeypatch, resp=None):
    """Patch the client; return a dict that captures the outbound kwargs."""
    captured = {}
    def fake_create(**kwargs):
        captured.update(kwargs)
        return resp or _Resp()
    monkeypatch.setattr(llm._client.messages, "create", fake_create)
    return captured


def test_llmresult_is_frozen():
    r = LLMResult(text="t", model="m", input_tokens=1, output_tokens=2,
                  stop_reason="end_turn")
    with pytest.raises(Exception):
        r.text = "x"          # frozen dataclass
```

- [ ] **Step 2: Run it, expect ImportError** — `LLMResult`/`call_detailed`/`_UNSET` do not exist yet.

Run: `pytest tests/test_llm_call_detailed.py::test_llmresult_is_frozen -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** in `shared/llm.py`. Add above `call()`:

```python
from dataclasses import dataclass

# Distinguishes "caller did not override effort" from "override to no effort".
# ROUTING stores None as a MEANINGFUL effort for care_gap/transparency/eval_judge,
# so a plain `effort=None` default would collide those two cases. Harmless for
# P2-4's two arms, but this is the one place model routing lives (spec §7).
_UNSET = object()


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str            # resp.model: the id that ACTUALLY ran, not requested
    input_tokens: int
    output_tokens: int
    stop_reason: str      # never "max_tokens": call_detailed raises first
```

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_llm_call_detailed.py::test_llmresult_is_frozen -v`

- [ ] **Step 5: Commit.**

```bash
git add shared/llm.py tests/test_llm_call_detailed.py
git commit -m "feat(P2-4): add LLMResult dataclass and _UNSET sentinel"
```

### Task 1.2: `call_detailed()` with overrides, and `call()` as a thin wrapper

- [ ] **Step 1: Write failing tests** (append to `tests/test_llm_call_detailed.py`)

```python
def test_call_detailed_returns_observed_model_and_tokens(monkeypatch):
    _capture(monkeypatch, _Resp(model="claude-opus-4-8-20260101",
                                input_tokens=7, output_tokens=9))
    r = call_detailed("coding", system="s", user="u", max_tokens=100)
    assert r.model == "claude-opus-4-8-20260101"   # resp.model, not requested
    assert r.input_tokens == 7 and r.output_tokens == 9
    assert r.text == "ok"


def test_call_detailed_model_override_reaches_the_outbound_request(monkeypatch):
    captured = _capture(monkeypatch)
    call_detailed("coding", system="s", user="u", max_tokens=100,
                  model="claude-opus-4-8")
    assert captured["model"] == "claude-opus-4-8"    # NOT ROUTING's default


def test_call_detailed_effort_override_reaches_the_outbound_request(monkeypatch):
    captured = _capture(monkeypatch)
    call_detailed("coding", system="s", user="u", max_tokens=100,
                  effort="high")
    assert captured["output_config"] == {"effort": "high"}


def test_default_model_and_effort_come_from_routing(monkeypatch):
    captured = _capture(monkeypatch)
    call_detailed("coding", system="s", user="u", max_tokens=100)
    # ROUTING["coding"] default is (claude-sonnet-5, xhigh).
    assert captured["model"] == "claude-sonnet-5"
    assert captured["output_config"] == {"effort": "xhigh"}


def test_unset_effort_is_distinct_from_explicit_none(monkeypatch):
    # eval_judge routes with effort None (a reasoning-free, temperature-pinned
    # call). _UNSET must fall back to ROUTING's None; explicit None must also be
    # honored, and neither may send output_config.
    captured = _capture(monkeypatch)
    call_detailed("eval_judge", system="s", user="u", max_tokens=100,
                  temperature=0)
    assert "output_config" not in captured           # ROUTING effort is None
    assert captured["temperature"] == 0


def test_call_detailed_raises_on_truncation_before_building_result(monkeypatch):
    _capture(monkeypatch, _Resp(stop_reason="max_tokens"))
    with pytest.raises(llm.TruncatedResponseError):
        call_detailed("coding", system="s", user="u", max_tokens=10)


def test_call_is_a_thin_wrapper_returning_text(monkeypatch):
    _capture(monkeypatch, _Resp(text="hello"))
    assert call("coding", system="s", user="u", max_tokens=100) == "hello"


def test_call_signature_is_unchanged_for_existing_callers(monkeypatch):
    # services/intake/structure.py calls call(component, system=, user=,
    # max_tokens=). tests/test_structure.py fakes exactly that shape. If call()'s
    # positional/keyword contract changes, those callers break.
    import inspect
    sig = inspect.signature(call)
    assert list(sig.parameters)[:4] == ["component", "system", "user", "max_tokens"]
```

- [ ] **Step 2: Run, expect FAIL** (`call_detailed` undefined). `pytest tests/test_llm_call_detailed.py -v`

- [ ] **Step 3: Implement** in `shared/llm.py`. Replace the existing `call()` body with `call_detailed()` plus a thin `call()`:

```python
def call_detailed(component: str, system: str, user: str,
                  max_tokens: int = 1500, temperature: float | None = None,
                  model: str | None = None, effort=_UNSET) -> LLMResult:
    """Route a component to a model and effort, return the full result.

    `model` and `effort` override ROUTING for this one call; that is how P2-4
    issues one arm as Sonnet-5-at-xhigh and the other as Opus-4.8-at-high while
    keeping model routing in this one module. `effort` uses the _UNSET sentinel,
    not None, because None is a meaningful configured effort (spec §7).

    temperature and effort are mutually exclusive at the API: an effort call is
    a reasoning call and samples for itself. Only effort-free components (the
    judge) may pin temperature.
    """
    default_model, default_effort = ROUTING[component]
    model = default_model if model is None else model
    effort = default_effort if effort is _UNSET else effort

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if effort:
        kwargs["output_config"] = {"effort": effort}
    elif temperature is not None:
        kwargs["temperature"] = temperature

    resp = _client.messages.create(**kwargs)

    if resp.stop_reason == "max_tokens":
        raise TruncatedResponseError(component, max_tokens)

    text = "".join(block.text for block in resp.content
                   if getattr(block, "type", None) == "text")
    return LLMResult(
        text=text,
        model=resp.model,                    # what actually ran
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        stop_reason=resp.stop_reason,
    )


def call(component: str, system: str, user: str,
         max_tokens: int = 1500, temperature: float | None = None) -> str:
    """Thin wrapper over call_detailed for callers that want only the text.

    Kept so no existing caller changes: call() returns str and discards the
    response, which is exactly why call_detailed exists (spec §7).
    """
    return call_detailed(component, system, user, max_tokens=max_tokens,
                         temperature=temperature).text
```

Note: `test_a_truncated_response_raises_an_actionable_error` in `tests/test_structure.py` builds a `_Resp` with `content=[]` and `stop_reason="max_tokens"` but **no `.usage`/`.model`**. `call_detailed` raises on `max_tokens` *before* reading `.usage`/`.model`, so that test still passes. Verify this in Step 4.

- [ ] **Step 4: Run the new file and the structure regression.**

Run: `pytest tests/test_llm_call_detailed.py tests/test_structure.py -v`
Expected: all PASS (including the pre-existing truncation and resampling tests).

- [ ] **Step 5: Commit.**

```bash
git add shared/llm.py tests/test_llm_call_detailed.py
git commit -m "feat(P2-4): call_detailed() with model/effort overrides; call() thin wrapper"
```

### Task 1.3: Extract `parse_and_enrich` in the coding agent

`run()` currently inlines the `extract_json` / non-dict / `ValidationError` guards plus `_enrich`. The benchmark needs the same parsing path without `run()`'s `log_decision` and `int` encounter_id (spec §7). Extract it; `run()` calls it. Behavior is unchanged, so every existing `tests/test_coding_agent.py` test must still pass.

- [ ] **Step 1: Write failing tests** (append to `tests/test_coding_agent.py`)

```python
# ---------- parse_and_enrich: the shared parsing path (P2-4 seam) ----------

def test_parse_and_enrich_matches_run_output(monkeypatch):
    """The benchmark uses parse_and_enrich directly, so it must produce exactly
    what run() produces from the same raw string, minus the logging."""
    raw = _one("ICD-10", "E11.9")
    out = coding_agent.parse_and_enrich(raw)
    assert out.codes[0].vocabulary_status == "verified"
    assert out.vocabulary_version == vocab.VOCAB_VERSION


def test_parse_and_enrich_raises_coding_error_on_bare_array():
    with pytest.raises(coding_agent.CodingError):
        coding_agent.parse_and_enrich('[{"system": "ICD-10"}]')


def test_parse_and_enrich_raises_coding_error_on_malformed_json():
    with pytest.raises(coding_agent.CodingError):
        coding_agent.parse_and_enrich("not json")
```

- [ ] **Step 2: Run, expect FAIL** (`parse_and_enrich` undefined). `pytest tests/test_coding_agent.py -k parse_and_enrich -v`

- [ ] **Step 3: Implement** in `services/agent_coding/agent.py`. Add `parse_and_enrich`, and rewrite `run()` to call it. The `TruncatedResponseError` catch stays in `run()` around the `call()`, because a truncation has no raw text to parse:

```python
def parse_and_enrich(raw: str) -> CodingOutput:
    """Turn one raw model response into a validated, vocabulary-classified
    CodingOutput. The single parsing path, shared by run() and P2-4, so a
    scoring difference can never be a parsing difference (spec §7).
    """
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
            f"did not match the ModelCodingPayload schema ({exc})", raw) from exc

    return _enrich(payload)
```

Then in `run()`, replace the inlined `extract_json`/isinstance/`ModelCodingPayload`/`_enrich` block with `out = parse_and_enrich(raw)`. Keep the `try/except TruncatedResponseError` around the `call(...)` exactly as is.

- [ ] **Step 4: Run the whole coding-agent suite** to prove behavior is unchanged.

Run: `pytest tests/test_coding_agent.py -v`
Expected: all PASS (the 20+ existing tests plus the 3 new ones).

- [ ] **Step 5: Lint and commit.**

```bash
ruff check shared/llm.py services/agent_coding/agent.py
git add services/agent_coding/agent.py tests/test_coding_agent.py
git commit -m "refactor(P2-4): extract parse_and_enrich shared by run() and the benchmark"
```

---

## Chunk 2: Dedup, denominators, and the floor

This chunk turns a per-note pair of `CodingOutput`s into the deduplicated per-code verdicts every rate is computed from, and the arm-specific floor band. It is pure and has no API or DB dependency. Get the conflict rule and the dedup key exactly right: they are, in the spec's words, "the single highest-leverage choice in the metric" (spec §1).

**Files:**
- Create: `governance/coding_metrics.py`
- Test: `tests/test_coding_metrics.py`

### Design locked here (spec §1, §5)

- Dedup key is `vocab.normalize(code)` **alone**, **per note**. Never `(system, code)`; never global.
- Every `system` label seen for a normalized code is retained (cause-3 attribution needs them).
- A deduped code's status is resolved from all its occurrences: `verified` if any occurrence verifies (it cannot co-occur with another status, since `classify` rules 1–2 ignore the label), else `not_found` if any occurrence is not_found, else `unchecked`. **`not_found` beats `unchecked`.**
- `checkable = verified + not_found` (excludes `unchecked`).
- Floor causes, per arm, over that arm's `not_found` codes:
  - cause 4 (degenerate input): normalized key fails `_CODE_SHAPE_RE` or is in `_PLACEHOLDERS`.
  - cause 3 (CPT-shaped, unverifiable): `vocab._looks_like_cpt(key)` and any retained label is not `"CPT"`. **Upper bound only** (spec §5b).
  - cause 2 (real but absent from pin): only if the §5a floor pin resolves it; else falls into the residual. Default (no FY2025 pin) leaves it in the residual.
  - cause 1 (fabricated): the residual.
  - Evaluate in order 4 → 3 → (2) → 1; each not_found code lands in exactly one cause.
- Floor band per arm: `lower = (cause4 + cause3 [+ cause2 if resolved]) / checkable * 100`; `upper = not_found_rate` (points). (spec §2, §5d)

- [ ] **Task 2.1 Step 1: Write failing tests** in `tests/test_coding_metrics.py`

```python
"""P2-4 dedup, denominators, and the floor. Pure; no API, no DB.

The conflict rule and the dedup key are the highest-leverage choices in the
metric (spec §1). These tests pin them hard.
"""
from __future__ import annotations

import pytest

from governance.coding_metrics import (
    DedupedCode, FloorBand, dedupe_note, floor_band, note_denominators,
    _CODE_SHAPE_RE, _PLACEHOLDERS,
)
from shared.schemas import CodeSuggestion, CodingOutput
from shared import vocab


def _out(*triples) -> CodingOutput:
    """Build a CodingOutput from (system, code) or (system, code, status) triples.
    Status defaults to what classify would assign, matching the real agent path.
    """
    codes = []
    for t in triples:
        system, code = t[0], t[1]
        status = t[2] if len(t) > 2 else vocab.classify(system, code)
        codes.append(CodeSuggestion(system=system, code=code, description="d",
                                    vocabulary_status=status))
    return CodingOutput(codes=codes, confidence=0.9,
                        vocabulary_version=vocab.VOCAB_VERSION)


def test_a_doubled_code_counts_once():
    # E11.9 twice within one note is one deduped observation.
    note = _out(("ICD-10", "E11.9"), ("ICD-10", "E11.9"))
    deduped = dedupe_note(note)
    assert len(deduped) == 1
    assert deduped[0].status == "verified"


def test_dedup_key_is_normalize_alone_not_system_plus_code():
    # Same code, two systems -> ONE deduped observation, not two.
    note = _out(("ICD-10", "E11.9"), ("CPT", "E11.9"))
    deduped = dedupe_note(note)
    assert len(deduped) == 1
    assert set(deduped[0].systems_seen) == {"ICD-10", "CPT"}


def test_not_found_beats_unchecked_on_conflict():
    # A code absent from both vocabularies, emitted once as CPT (unchecked) and
    # once as ICD-10 (not_found), resolves to not_found (spec §1).
    note = _out(("CPT", "88888"), ("ICD-10", "88888"))
    deduped = dedupe_note(note)
    assert len(deduped) == 1
    assert deduped[0].status == "not_found"


def test_verified_never_conflicts():
    # E11.9 verifies under any label; a second CPT-labelled occurrence cannot
    # demote it, because classify rules 1-2 ignore the label.
    note = _out(("CPT", "E11.9"), ("ICD-10", "E11.9"))
    assert dedupe_note(note)[0].status == "verified"


def test_denominators_exclude_unchecked_from_checkable():
    note = _out(("ICD-10", "E11.9"),      # verified
                ("ICD-10", "M9999"),      # not_found
                ("CPT", "99213"))         # unchecked
    d = note_denominators(dedupe_note(note))
    assert d.verified == 1 and d.not_found == 1 and d.unchecked == 1
    assert d.checkable == 2 and d.total == 3


def test_placeholders_are_cause_4_not_cause_1():
    for token in ("NONE", "UNKNOWN", "TBD"):
        assert token in _PLACEHOLDERS
        note = _out(("ICD-10", token))          # not_found, degenerate
        band = floor_band(dedupe_note(note))
        assert band.cause4 == 1 and band.cause1 == 0


def test_slash_form_na_is_cause_4_by_shape():
    # normalize strips only the dot, so "N/A" keeps its slash and fails shape.
    assert not _CODE_SHAPE_RE.match(vocab.normalize("N/A"))
    band = floor_band(dedupe_note(_out(("ICD-10", "N/A"))))
    assert band.cause4 == 1


def test_cpt_shaped_not_found_is_cause_3_upper_bound():
    # 99213 declared ICD-10 is not_found (real CPT mislabelled), CPT-shaped ->
    # cause 3. It is an UPPER bound: a fabricated 5-digit ICD-10 code is
    # indistinguishable and lands here too.
    band = floor_band(dedupe_note(_out(("ICD-10", "99213"))))
    assert band.cause3 == 1 and band.cause1 == 0


def test_cpt_shape_predicate_is_inclusive_over_retained_labels():
    # A code carrying several labels satisfies cause 3 if ANY retained label is
    # not CPT (spec §5b). 99213 as {CPT, ICD-10}, absent from vocab, resolves
    # not_found (ICD-10 occurrence) and is CPT-shaped with a non-CPT label.
    note = _out(("CPT", "99213"), ("ICD-10", "99213"))
    deduped = dedupe_note(note)
    assert deduped[0].status == "not_found"
    assert floor_band(deduped).cause3 == 1


def test_fabricated_icd_shaped_code_is_cause_1_residual():
    # M9999: ICD-shaped, not CPT-shaped, not degenerate, absent -> fabricated.
    band = floor_band(dedupe_note(_out(("ICD-10", "M9999"))))
    assert band.cause1 == 1
    assert band.cause3 == 0 and band.cause4 == 0


def test_floor_band_bounds_are_percentage_points():
    # 1 verified + 1 not_found(fabricated) => checkable 2, not_found_rate 50 pts.
    note = _out(("ICD-10", "E11.9"), ("ICD-10", "M9999"))
    band = floor_band(dedupe_note(note))
    assert band.upper == pytest.approx(50.0)      # points, not 0.5
    assert band.lower == pytest.approx(0.0)       # no cause 3/4 here
```

- [ ] **Task 2.1 Step 2: Run, expect FAIL** (module missing). `pytest tests/test_coding_metrics.py -v`

- [ ] **Task 2.1 Step 3: Implement** `governance/coding_metrics.py`:

```python
"""Deduplicate a note's suggested codes, compute denominators, attribute the
floor. Pure: no API, no DB. Consumes CodingOutput, uses only shared.vocab.

Every counting rule here is pinned by the P2-4 spec §1 and §5. The two that
matter most: dedup is on vocab.normalize(code) ALONE and PER NOTE, and a
not_found/unchecked conflict resolves to not_found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from shared import vocab
from shared.schemas import CodingOutput
from shared.vocab import VocabularyStatus

# Benchmark-side heuristics, documented as such (spec §5c). shared.vocab has
# normalize and _CPT_RE and no general shape test, so these live here.
_CODE_SHAPE_RE = re.compile(r"^[A-Z0-9]{3,7}$")
# normalize uppercases and strips the DOT ONLY, so these survive as bare
# alphanumerics, pass a shape test, and would otherwise inflate cause 1.
_PLACEHOLDERS = frozenset({"NONE", "UNKNOWN", "TBD", "NA", "NIL", "PENDING"})

# not_found outranks unchecked; verified cannot co-occur with either.
_STATUS_RANK = {"not_found": 2, "unchecked": 1}


@dataclass(frozen=True)
class DedupedCode:
    key: str                          # vocab.normalize(code)
    status: VocabularyStatus
    systems_seen: tuple[str, ...]     # every declared system, retained
    descriptions: tuple[str, ...]     # model prose, for the roster only


@dataclass(frozen=True)
class Denominators:
    verified: int
    not_found: int
    unchecked: int

    @property
    def checkable(self) -> int:
        return self.verified + self.not_found

    @property
    def total(self) -> int:
        return self.verified + self.not_found + self.unchecked


@dataclass(frozen=True)
class FloorBand:
    cause1: int          # fabricated (residual)
    cause2: int          # real but absent from pin (0 unless a floor pin resolves it)
    cause3: int          # CPT-shaped, unverifiable (UPPER bound)
    cause4: int          # degenerate input
    checkable: int
    not_found: int

    @property
    def lower(self) -> float:
        """Lower bound on the floor, percentage points: the decidable artifact
        causes as a share of checkable codes. cause2 is included only when a
        floor pin resolved it (default 0)."""
        if not self.checkable:
            return 0.0
        return 100.0 * (self.cause4 + self.cause3 + self.cause2) / self.checkable

    @property
    def upper(self) -> float:
        """Upper bound on the floor, percentage points: the full not-found rate.
        Every not_found code is one of the four causes."""
        if not self.checkable:
            return 0.0
        return 100.0 * self.not_found / self.checkable


def dedupe_note(out: CodingOutput) -> list[DedupedCode]:
    """Collapse a note's codes on normalize(code) alone. Order is stable by
    first appearance so the roster and any diff are deterministic."""
    order: list[str] = []
    groups: dict[str, dict] = {}
    for c in out.codes:
        key = vocab.normalize(c.code)
        if key not in groups:
            groups[key] = {"statuses": [], "systems": [], "descriptions": []}
            order.append(key)
        # Recompute status from (system, code); never trust the stored one when
        # dedupe could merge differently-labelled occurrences.
        groups[key]["statuses"].append(vocab.classify(c.system, c.code))
        groups[key]["systems"].append(c.system)
        groups[key]["descriptions"].append(c.description)

    deduped: list[DedupedCode] = []
    for key in order:
        g = groups[key]
        deduped.append(DedupedCode(
            key=key,
            status=_resolve(g["statuses"]),
            systems_seen=tuple(g["systems"]),
            descriptions=tuple(g["descriptions"]),
        ))
    return deduped


def _resolve(statuses: list[VocabularyStatus]) -> VocabularyStatus:
    if "verified" in statuses:
        return "verified"          # cannot conflict; wins trivially
    return max(statuses, key=lambda s: _STATUS_RANK[s])   # not_found > unchecked


def note_denominators(deduped: list[DedupedCode]) -> Denominators:
    v = sum(c.status == "verified" for c in deduped)
    nf = sum(c.status == "not_found" for c in deduped)
    un = sum(c.status == "unchecked" for c in deduped)
    return Denominators(verified=v, not_found=nf, unchecked=un)


def _cause_of(code: DedupedCode) -> int:
    """Attribute one not_found code to a floor cause. Order 4 -> 3 -> 1.
    cause 2 needs a prior-release pin and is handled by the caller when a floor
    pin is supplied; here it never fires, so cause-2 codes fall to cause 1."""
    key = code.key
    if key in _PLACEHOLDERS or not _CODE_SHAPE_RE.match(key):
        return 4
    if vocab._looks_like_cpt(key) and any(s != "CPT" for s in code.systems_seen):
        return 3
    return 1


def floor_band(deduped: list[DedupedCode], floor_members=None) -> FloorBand:
    """Attribute this arm's not_found codes to floor causes.

    floor_members: optional frozenset of normalized keys known to be real in a
    prior release but absent from the current pin (spec §5a). When supplied, a
    not_found code in it is cause 2 instead of cause 1. Default None keeps cause
    2 empty and folded into the residual, which is the approved default.
    """
    denom = note_denominators(deduped)
    c1 = c2 = c3 = c4 = 0
    for code in deduped:
        if code.status != "not_found":
            continue
        cause = _cause_of(code)
        if cause == 1 and floor_members is not None and code.key in floor_members:
            cause = 2
        c1 += cause == 1
        c2 += cause == 2
        c3 += cause == 3
        c4 += cause == 4
    return FloorBand(cause1=c1, cause2=c2, cause3=c3, cause4=c4,
                     checkable=denom.checkable, not_found=denom.not_found)
```

- [ ] **Task 2.1 Step 4: Run, expect PASS.** `pytest tests/test_coding_metrics.py -v`

- [ ] **Task 2.1 Step 5: Lint and commit.**

```bash
ruff check governance/coding_metrics.py
git add governance/coding_metrics.py tests/test_coding_metrics.py
git commit -m "feat(P2-4): per-note dedup, denominators, and floor attribution"
```

### Task 2.2: Pooled per-arm aggregation and agreement

Aggregate deduped per-note results across an analysis set into per-arm rates (pooled ratio-of-sums, in points), and compute per-note Jaccard agreement. Agreement is descriptive only and never an input to routing (spec §1).

- [ ] **Step 1: Write failing tests** (append to `tests/test_coding_metrics.py`)

```python
from governance.coding_metrics import ArmSummary, aggregate_arm, note_agreement


def test_aggregate_is_pooled_ratio_of_sums_in_points():
    # Note A: 1 verified, 1 not_found. Note B: 2 verified, 0 not_found.
    # Pooled verified rate = 3/4 = 75 pts; not_found rate = 25 pts.
    notes = [
        dedupe_note(_out(("ICD-10", "E11.9"), ("ICD-10", "M9999"))),
        dedupe_note(_out(("ICD-10", "E11.9"), ("ICD-10", "I10"))),
    ]
    s = aggregate_arm(notes)
    assert s.verified_rate == pytest.approx(75.0)
    assert s.not_found_rate == pytest.approx(25.0)
    assert s.n_notes == 2 and s.checkable == 4


def test_verified_rate_is_none_on_empty_checkable():
    # All unchecked -> checkable 0 -> rate None, never 0.0 (spec §1, verified_rate).
    notes = [dedupe_note(_out(("CPT", "99213")))]
    s = aggregate_arm(notes)
    assert s.verified_rate is None and s.not_found_rate is None


def test_pessimistic_counts_unchecked_as_not_verified():
    # 1 verified, 1 unchecked -> standard verified rate 100 pts (unchecked
    # excluded); pessimistic 50 pts (unchecked counted against).
    notes = [dedupe_note(_out(("ICD-10", "E11.9"), ("CPT", "99213")))]
    s = aggregate_arm(notes)
    assert s.verified_rate == pytest.approx(100.0)
    assert s.pessimistic_verified_rate == pytest.approx(50.0)
    assert s.unchecked_share == pytest.approx(50.0)


def test_agreement_is_jaccard_over_normalized_keys():
    a = dedupe_note(_out(("ICD-10", "E11.9"), ("ICD-10", "I10")))
    b = dedupe_note(_out(("ICD-10", "E11.9"), ("ICD-10", "M9999")))
    assert note_agreement(a, b) == pytest.approx(1 / 3)   # {E119} / {E119,I10,M9999}


def test_agreement_is_none_when_neither_arm_emitted_a_code():
    assert note_agreement([], []) is None
```

- [ ] **Step 2: Run, expect FAIL.** `pytest tests/test_coding_metrics.py -k "aggregate or agreement or pessimistic or verified_rate_is_none" -v`

- [ ] **Step 3: Implement** (append to `governance/coding_metrics.py`):

```python
@dataclass(frozen=True)
class ArmSummary:
    n_notes: int
    verified: int
    not_found: int
    unchecked: int
    checkable: int
    total: int
    codes_per_note: float

    @property
    def verified_rate(self) -> float | None:
        r = vocab.verified_rate(self.verified, self.not_found)
        return None if r is None else 100.0 * r

    @property
    def not_found_rate(self) -> float | None:
        r = self.verified_rate
        return None if r is None else 100.0 - r

    @property
    def pessimistic_verified_rate(self) -> float | None:
        if self.total == 0:
            return None
        return 100.0 * self.verified / self.total

    @property
    def unchecked_share(self) -> float | None:
        if self.total == 0:
            return None
        return 100.0 * self.unchecked / self.total


def aggregate_arm(notes: list[list[DedupedCode]]) -> ArmSummary:
    """Pool deduped per-note results into per-arm counts (spec §4 estimator:
    a ratio of sums, never a mean of per-note rates)."""
    v = nf = un = 0
    for note in notes:
        d = note_denominators(note)
        v += d.verified
        nf += d.not_found
        un += d.unchecked
    total = v + nf + un
    n = len(notes)
    return ArmSummary(n_notes=n, verified=v, not_found=nf, unchecked=un,
                      checkable=v + nf, total=total,
                      codes_per_note=(total / n if n else 0.0))


def note_agreement(a: list[DedupedCode], b: list[DedupedCode]) -> float | None:
    """Per-note Jaccard over normalized keys. None when neither arm emitted a
    code, so it never enters an average as a spurious 0 or 1 (spec §1)."""
    ka = {c.key for c in a}
    kb = {c.key for c in b}
    if not ka and not kb:
        return None
    return len(ka & kb) / len(ka | kb)
```

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_coding_metrics.py -v`

- [ ] **Step 5: Lint and commit.**

```bash
ruff check governance/coding_metrics.py
git add governance/coding_metrics.py tests/test_coding_metrics.py
git commit -m "feat(P2-4): pooled per-arm aggregation and per-note agreement"
```

---

## Chunk 3: The note-level paired BCa bootstrap

The interval on the paired difference in not-found rate. Codes cluster within notes, so binomial intervals over codes are wrong; the bootstrap resamples **notes**, both arms sharing the resampled indices within each replicate (spec §4). BCa is hand-rolled because `scipy.stats.bootstrap(method="BCa")` uses the naive strict-`<` bias correction and does not implement the pinned tie convention (spec §4). This is pure numerical code and the highest-risk arithmetic in the benchmark: run it at max effort and test the degenerate cases explicitly.

**Files:**
- Create: `governance/coding_bootstrap.py`
- Test: `tests/test_coding_bootstrap.py`

### Design locked here (spec §4)

- Estimand: `delta = nf_A/checkable_A - nf_B/checkable_B`, a difference of ratios-of-sums (**not** a mean of per-note differences), computed on the full analysis set for the point estimate and on each resample for the replicates.
- Internally in **proportion**; the public result multiplies `d` and both CI endpoints by 100 to **points** at the return boundary (spec §2: delta_b yields a proportion, multiplied by 100 before the decision compares it).
- Shared indices: `rng.integers(0, n, size=n)` drawn once per replicate and used for **both** arms.
- Replicates whose resample gives a zero checkable sum for either arm yield `None` from the ratio and are **dropped**; `B` (the retained count) is what enters the `z0` denominator. The dropped count is recorded.
- `z0 = Phi^-1((#{delta_b < d} + 0.5*#{delta_b == d}) / B)` — mid-rank ties.
- Acceleration: leave-one-note-out jackknife over the analysis set, `a = sum(u_i^3) / (6*(sum(u_i^2))^1.5)`, `u_i = mean(jack) - jack_i`. Zero denominator → `a = 0` **and** record `acceleration_degenerate = True` (never silently).
- Seed and replicate count recorded.

- [ ] **Task 3.1 Step 1: Write failing tests** in `tests/test_coding_bootstrap.py`

```python
"""P2-4 note-level paired BCa bootstrap. Pure numerical, no API, no DB.

The load-bearing property (spec §4): with two identical arms, EVERY replicate
delta must be exactly 0, because both arms share the resampled indices. That is
what the shared-index design guarantees, and it is what an independent-resample
bug would break (widening the interval toward the branch this spec predicts).
"""
from __future__ import annotations

import numpy as np
import pytest

from governance.coding_bootstrap import (
    NotePair, ratio_diff, replicate_deltas, paired_bootstrap_bca,
)


def _pair(nf_a, ck_a, nf_b, ck_b):
    return NotePair(nf_a=nf_a, checkable_a=ck_a, nf_b=nf_b, checkable_b=ck_b)


def test_ratio_diff_is_a_difference_of_ratios_of_sums():
    # nf_a=3, ck_a=4 -> 0.75; nf_b=1, ck_b=5 -> 0.2; diff 0.55 (proportion).
    assert ratio_diff(3, 4, 1, 5) == pytest.approx(0.55)


def test_ratio_diff_is_none_on_zero_denominator():
    assert ratio_diff(0, 0, 1, 5) is None
    assert ratio_diff(3, 4, 0, 0) is None


def test_identical_arms_give_every_replicate_delta_exactly_zero():
    pairs = [_pair(1, 3, 1, 3), _pair(2, 4, 2, 4), _pair(0, 2, 0, 2)]
    rng = np.random.default_rng(123)
    deltas, dropped = replicate_deltas(pairs, rng, replicates=500)
    assert dropped == 0
    assert np.all(deltas == 0.0), "shared indices must cancel identical arms exactly"


def test_identical_arms_yield_a_zero_point_estimate_and_zero_width_ci():
    pairs = [_pair(1, 3, 1, 3), _pair(2, 4, 2, 4)]
    res = paired_bootstrap_bca(pairs, seed=7, replicates=500)
    assert res.d == pytest.approx(0.0)
    assert res.ci == pytest.approx((0.0, 0.0))
    assert res.acceleration_degenerate is True   # all jackknife reps equal -> 0/0
    assert res.acceleration == 0.0


def test_result_is_in_points_not_proportion():
    # Arm A all not_found, arm B all verified: proportion diff 1.0 -> 100 points.
    pairs = [_pair(2, 2, 0, 2), _pair(3, 3, 0, 3)]
    res = paired_bootstrap_bca(pairs, seed=1, replicates=500)
    assert res.d == pytest.approx(100.0)


def test_seed_and_replicate_count_are_recorded_and_reproducible():
    pairs = [_pair(1, 3, 0, 3), _pair(2, 4, 1, 4), _pair(0, 2, 1, 2)]
    a = paired_bootstrap_bca(pairs, seed=42, replicates=1000)
    b = paired_bootstrap_bca(pairs, seed=42, replicates=1000)
    assert a.seed == 42 and a.replicates == 1000
    assert a.ci == pytest.approx(b.ci)          # same seed -> same interval


def test_zero_denominator_replicates_are_dropped_and_counted():
    # One note has checkable 0 for arm A; some resamples draw only that note,
    # giving a None replicate that must be dropped, not treated as 0.
    pairs = [_pair(0, 0, 0, 1), _pair(1, 2, 0, 2)]
    res = paired_bootstrap_bca(pairs, seed=3, replicates=2000)
    assert res.dropped > 0
    assert res.retained + res.dropped == 2000
    assert res.retained > 0


def test_empty_denominator_point_estimate_raises():
    with pytest.raises(ValueError, match="zero checkable"):
        paired_bootstrap_bca([_pair(0, 0, 0, 0)], seed=1, replicates=10)


def test_acceleration_degenerate_flag_is_false_on_normal_data():
    pairs = [_pair(1, 3, 0, 3), _pair(2, 4, 1, 5), _pair(0, 2, 2, 4),
             _pair(3, 5, 1, 3)]
    res = paired_bootstrap_bca(pairs, seed=9, replicates=2000)
    assert res.acceleration_degenerate is False
```

- [ ] **Task 3.1 Step 2: Run, expect FAIL** (module missing). `pytest tests/test_coding_bootstrap.py -v`

- [ ] **Task 3.1 Step 3: Implement** `governance/coding_bootstrap.py`:

```python
"""Note-level paired BCa bootstrap for the difference in not-found rate.

Pure numerical. Everything internal is on the PROPORTION scale; the public
result multiplies d and the CI endpoints by 100 to percentage points at the
return boundary (spec §2, §4). BCa is hand-rolled: scipy's BCa uses the naive
strict-< bias correction and does not implement the mid-rank tie convention
this benchmark pins.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


@dataclass(frozen=True)
class NotePair:
    """One analysis-set note's not_found and checkable counts for both arms."""
    nf_a: int
    checkable_a: int
    nf_b: int
    checkable_b: int


@dataclass(frozen=True)
class BootResult:
    d: float                       # point estimate, percentage points
    ci: tuple[float, float]        # 95% BCa interval, percentage points
    seed: int
    replicates: int                # requested
    retained: int                  # replicates that survived (B in z0 denom)
    dropped: int                   # zero-denominator replicates dropped
    acceleration: float
    acceleration_degenerate: bool


def ratio_diff(nf_a_sum: float, ck_a_sum: float,
               nf_b_sum: float, ck_b_sum: float) -> float | None:
    """Difference of ratios-of-sums, proportion scale. None on a zero denom for
    either arm (matches vocab.verified_rate returning None, never 0.0)."""
    if ck_a_sum == 0 or ck_b_sum == 0:
        return None
    return nf_a_sum / ck_a_sum - nf_b_sum / ck_b_sum


def _arrays(pairs: list[NotePair]):
    return (np.array([p.nf_a for p in pairs], float),
            np.array([p.checkable_a for p in pairs], float),
            np.array([p.nf_b for p in pairs], float),
            np.array([p.checkable_b for p in pairs], float))


def _stat(nf_a, ck_a, nf_b, ck_b, idx) -> float | None:
    return ratio_diff(nf_a[idx].sum(), ck_a[idx].sum(),
                      nf_b[idx].sum(), ck_b[idx].sum())


def replicate_deltas(pairs: list[NotePair], rng: np.random.Generator,
                     replicates: int) -> tuple[np.ndarray, int]:
    """Bootstrap replicate deltas (proportion) with SHARED indices per replicate.
    Returns (kept_deltas, dropped_count)."""
    nf_a, ck_a, nf_b, ck_b = _arrays(pairs)
    n = len(pairs)
    kept: list[float] = []
    dropped = 0
    for _ in range(replicates):
        idx = rng.integers(0, n, size=n)          # one draw, used for BOTH arms
        s = _stat(nf_a, ck_a, nf_b, ck_b, idx)
        if s is None:
            dropped += 1
        else:
            kept.append(s)
    return np.array(kept, float), dropped


def paired_bootstrap_bca(pairs: list[NotePair], seed: int,
                         replicates: int = 10000,
                         alpha: float = 0.05) -> BootResult:
    """95% BCa interval on the paired not-found-rate difference, in points."""
    nf_a, ck_a, nf_b, ck_b = _arrays(pairs)
    n = len(pairs)
    full = np.arange(n)

    d_prop = _stat(nf_a, ck_a, nf_b, ck_b, full)
    if d_prop is None:
        raise ValueError(
            "analysis set has zero checkable codes for an arm; no point estimate")

    rng = np.random.default_rng(seed)
    deltas, dropped = replicate_deltas(pairs, rng, replicates)
    B = len(deltas)
    if B == 0:
        raise ValueError("every bootstrap replicate was dropped (zero denom)")

    # z0: mid-rank tie convention over the RETAINED replicates.
    less = float(np.sum(deltas < d_prop))
    eq = float(np.sum(deltas == d_prop))
    z0 = norm.ppf((less + 0.5 * eq) / B)

    # Acceleration: leave-one-note-out jackknife over the analysis set.
    jack = [_stat(nf_a, ck_a, nf_b, ck_b, np.delete(full, i)) for i in range(n)]
    jack = np.array([j for j in jack if j is not None], float)
    u = jack.mean() - jack
    num = float(np.sum(u ** 3))
    den = 6.0 * float(np.sum(u ** 2)) ** 1.5
    if den == 0.0:
        a, a_degenerate = 0.0, True
    else:
        a, a_degenerate = num / den, False

    # BCa-adjusted percentiles.
    def _adj(z_alpha: float) -> float:
        num_z = z0 + z_alpha
        return float(norm.cdf(z0 + num_z / (1.0 - a * num_z)))

    a1 = _adj(norm.ppf(alpha / 2.0))
    a2 = _adj(norm.ppf(1.0 - alpha / 2.0))
    lo = float(np.quantile(deltas, a1, method="linear"))
    hi = float(np.quantile(deltas, a2, method="linear"))

    return BootResult(
        d=100.0 * d_prop,
        ci=(100.0 * lo, 100.0 * hi),
        seed=seed,
        replicates=replicates,
        retained=B,
        dropped=dropped,
        acceleration=a,
        acceleration_degenerate=a_degenerate,
    )
```

- [ ] **Task 3.1 Step 4: Run, expect PASS.** `pytest tests/test_coding_bootstrap.py -v`

Watch `test_identical_arms_give_every_replicate_delta_exactly_zero`: identical arms make `_stat` return `X - X` for the same summed index set, which is exactly `0.0` in float (identical operands, identical rounding), so `np.all(deltas == 0.0)` holds. If it ever fails, the arms were resampled independently, which is the bug the test exists to catch.

- [ ] **Task 3.1 Step 5: Lint and commit.**

```bash
ruff check governance/coding_bootstrap.py
git add governance/coding_bootstrap.py tests/test_coding_bootstrap.py
git commit -m "feat(P2-4): note-level paired BCa bootstrap with pinned tie convention"
```

---

## Chunk 4: The pre-registered decision rule and guards

The literal rule from spec §2. The wording *is* the rule, so implement it exactly: `abs(d)` not `d`, the Inconclusive branch present, guards evaluated first, and the intersection-loss guard **voids** rather than returning Inconclusive. This module decides only the **quality** branch and names the lower-not-found arm on a Difference; cost routing (which config wins under Equivalent/Inconclusive) is applied later with the price table (Chunk 6), because a missing price table is a terminal state (spec §2, §8).

**Files:**
- Create: `governance/coding_decision.py`
- Test: `tests/test_coding_decision.py`

### Constants and thresholds locked here (spec §2)

- `DELTA = 1.5` points — pre-registered, never recomputed, a module constant (not a parameter) so a caller cannot mis-scale it.
- `UNCHECKED_GUARD = 1.6` points; `VOLUME_GUARD = 0.25` (dimensionless ratio); `INTERSECTION_FLOOR = 0.90` of 120 notes (dimensionless ratio).
- Guards → **Inconclusive** with the guard named, except intersection loss → **Void**.

- [ ] **Task 4.1 Step 1: Write failing tests** in `tests/test_coding_decision.py`

```python
"""P2-4 pre-registered decision rule. Pure. The literal wording is the rule
(spec §2): abs(d), the Inconclusive branch, guards first, intersection loss
voids rather than returning Inconclusive.
"""
from __future__ import annotations

import pytest

from governance.coding_decision import (
    DELTA, ArmGuardStats, decide, Branch,
)


def _stats(unchecked_share, codes_per_note, floor_lower, floor_upper):
    return ArmGuardStats(unchecked_share=unchecked_share,
                         codes_per_note=codes_per_note,
                         floor_lower=floor_lower, floor_upper=floor_upper)


# Two well-behaved arms whose guards never trip for the branch tests. Floors are
# TIGHT (gap 0.1) so max_possible_floor_gap stays under the small |d| used below;
# a wide floor band would trip the floor guard and force Inconclusive, masking the
# branch logic these tests exist to exercise.
A = _stats(unchecked_share=20.0, codes_per_note=5.0, floor_lower=5.9, floor_upper=6.0)
B = _stats(unchecked_share=20.5, codes_per_note=5.1, floor_lower=5.9, floor_upper=6.0)

# Equal, zero-width floors: max_possible_floor_gap is exactly 0, so the floor
# guard cannot trip even at d == 0. Used only by the scale test.
FLAT = _stats(unchecked_share=20.0, codes_per_note=5.0, floor_lower=6.0, floor_upper=6.0)


def test_delta_is_the_pre_registered_points_constant():
    assert DELTA == 1.5


def test_ci_within_margin_is_equivalent_route_on_cost():
    r = decide(d=0.2, ci=(-1.0, 1.0), arm_a=A, arm_b=B, n_analysis=120,
               nf_rate_a=6.0, nf_rate_b=6.2)
    assert r.branch is Branch.EQUIVALENT
    assert r.route_on == "cost"


def test_ci_excludes_zero_and_abs_d_over_delta_is_difference():
    # d = -5 (arm B lower not-found), CI clear of zero and |d|>1.5 -> Difference,
    # route to the lower-not-found arm (B).
    r = decide(d=-5.0, ci=(-7.0, -3.0), arm_a=A, arm_b=B, n_analysis=120,
               nf_rate_a=11.0, nf_rate_b=6.0)
    assert r.branch is Branch.DIFFERENCE
    assert r.winner_arm == "B"          # lower not-found rate
    assert r.route_on == "quality"


def test_uses_abs_d_not_d_so_a_clear_arm_b_win_is_a_difference():
    # An earlier draft wrote 'point estimate exceeds delta', sending d=-5 to
    # Inconclusive. abs(d) is the rule.
    r = decide(d=-5.0, ci=(-7.0, -3.0), arm_a=A, arm_b=B, n_analysis=120,
               nf_rate_a=11.0, nf_rate_b=6.0)
    assert r.branch is Branch.DIFFERENCE


def test_ci_straddling_zero_but_wide_is_inconclusive():
    r = decide(d=0.5, ci=(-2.0, 3.0), arm_a=A, arm_b=B, n_analysis=120,
               nf_rate_a=6.0, nf_rate_b=5.5)
    assert r.branch is Branch.INCONCLUSIVE
    assert r.route_on == "cost"


def test_a_points_scale_ci_of_three_points_is_not_equivalent():
    # Guards the scale (spec §2). A real ±3-point CI is NOT within ±1.5, so it is
    # not Equivalent. This passes ONLY because DELTA is points; if DELTA were
    # read as a proportion (1.5 ~ 150 points) the CI would fit and wrongly
    # return Equivalent, which is the 'every run returns Equivalent' failure.
    # FLAT arms keep the floor guard from tripping at d==0, so the branch logic
    # (not a guard) decides the outcome.
    r = decide(d=0.0, ci=(-3.0, 3.0), arm_a=FLAT, arm_b=FLAT, n_analysis=120,
               nf_rate_a=6.0, nf_rate_b=6.0)
    assert r.branch is not Branch.EQUIVALENT


# ---------- guards evaluate first and force Inconclusive ----------

def test_unchecked_divergence_guard_forces_inconclusive():
    # Tight, equal floors so ONLY the unchecked guard trips, not the floor guard.
    a = _stats(10.0, 5.0, 5.9, 6.0)
    b = _stats(12.0, 5.0, 5.9, 6.0)      # unchecked gap 2.0 > 1.6
    r = decide(d=0.2, ci=(-1.0, 1.0), arm_a=a, arm_b=b, n_analysis=120,
               nf_rate_a=6.0, nf_rate_b=6.2)
    assert r.branch is Branch.INCONCLUSIVE
    assert "unchecked_divergence" in r.guards_tripped
    assert r.route_on == "cost"


def test_volume_divergence_guard_is_symmetric_on_the_mean():
    # cpn 4 vs 6: |4-6| / ((4+6)/2) = 2/5 = 0.4 > 0.25. Tight equal floors keep
    # the floor guard quiet so only the volume guard is under test.
    a = _stats(20.0, 4.0, 5.9, 6.0)
    b = _stats(20.0, 6.0, 5.9, 6.0)
    r = decide(d=0.2, ci=(-1.0, 1.0), arm_a=a, arm_b=b, n_analysis=120,
               nf_rate_a=6.0, nf_rate_b=6.2)
    assert "volume_divergence" in r.guards_tripped


def test_floor_divergence_guard_uses_max_possible_gap():
    # max_possible_floor_gap = max(upper(A)-lower(B), upper(B)-lower(A), 0).
    # A: lower 1, upper 9; B: lower 1, upper 6 -> gap max(9-1, 6-1, 0)=8 > |d|=2.
    a = _stats(20.0, 5.0, 1.0, 9.0)
    b = _stats(20.0, 5.0, 1.0, 6.0)
    r = decide(d=-2.0, ci=(-3.5, -0.5), arm_a=a, arm_b=b, n_analysis=120,
               nf_rate_a=8.0, nf_rate_b=6.0)
    assert "floor_divergence" in r.guards_tripped
    assert r.branch is Branch.INCONCLUSIVE


def test_intersection_loss_voids_rather_than_inconclusive():
    r = decide(d=0.2, ci=(-1.0, 1.0), arm_a=A, arm_b=B, n_analysis=100,  # <108
               nf_rate_a=6.0, nf_rate_b=6.2)
    assert r.branch is Branch.VOID
    assert "intersection_loss" in r.guards_tripped


def test_intersection_at_exactly_the_floor_is_not_voided():
    # 90% of 120 = 108. The guard fires BELOW 108, so 108 is allowed.
    r = decide(d=0.2, ci=(-1.0, 1.0), arm_a=A, arm_b=B, n_analysis=108,
               nf_rate_a=6.0, nf_rate_b=6.2)
    assert r.branch is not Branch.VOID
```

- [ ] **Task 4.1 Step 2: Run, expect FAIL.** `pytest tests/test_coding_decision.py -v`

- [ ] **Task 4.1 Step 3: Implement** `governance/coding_decision.py`:

```python
"""The P2-4 pre-registered decision rule and guards (spec §2). Pure.

Decides the QUALITY branch and names the lower-not-found arm on a Difference.
Cost routing under Equivalent/Inconclusive is applied by the caller with the
price table, because a missing price table is a terminal state (spec §8).

The literal wording is the rule. abs(d), not d. The Inconclusive branch is
present so an underpowered null and a genuine null do not collapse to the same
action. Intersection loss voids the run; the other guards return Inconclusive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Pre-registered constants (points except the two dimensionless ratios). Never
# recomputed from data; that would weaken the pre-registration (spec §4).
DELTA = 1.5                 # points
UNCHECKED_GUARD = 1.6       # points
VOLUME_GUARD = 0.25         # dimensionless ratio
INTERSECTION_FLOOR = 0.90   # dimensionless ratio, of the 120-note target
TARGET_NOTES = 120


class Branch(Enum):
    EQUIVALENT = "equivalent"
    DIFFERENCE = "difference"
    INCONCLUSIVE = "inconclusive"
    VOID = "void"


@dataclass(frozen=True)
class ArmGuardStats:
    unchecked_share: float      # points
    codes_per_note: float
    floor_lower: float          # points
    floor_upper: float          # points


@dataclass(frozen=True)
class Decision:
    branch: Branch
    guards_tripped: list[str] = field(default_factory=list)
    route_on: str | None = None          # "cost", "quality", or None (Void)
    winner_arm: str | None = None        # "A"/"B" on a Difference
    framing_disagreement: bool = False
    reason: str = ""


def _guards(arm_a: ArmGuardStats, arm_b: ArmGuardStats,
            n_analysis: int, abs_d: float) -> list[str]:
    tripped: list[str] = []

    # Intersection loss VOIDS; it is checked by the caller too, but naming it
    # here keeps the guard list complete.
    if n_analysis < INTERSECTION_FLOOR * TARGET_NOTES:
        tripped.append("intersection_loss")

    if abs(arm_a.unchecked_share - arm_b.unchecked_share) > UNCHECKED_GUARD:
        tripped.append("unchecked_divergence")

    mean_cpn = (arm_a.codes_per_note + arm_b.codes_per_note) / 2.0
    if mean_cpn > 0 and abs(arm_a.codes_per_note - arm_b.codes_per_note) / mean_cpn > VOLUME_GUARD:
        tripped.append("volume_divergence")

    max_gap = max(arm_a.floor_upper - arm_b.floor_lower,
                  arm_b.floor_upper - arm_a.floor_lower, 0.0)
    if max_gap > abs_d:
        tripped.append("floor_divergence")

    return tripped


def decide(*, d: float, ci: tuple[float, float],
           arm_a: ArmGuardStats, arm_b: ArmGuardStats,
           n_analysis: int, nf_rate_a: float, nf_rate_b: float,
           pessimistic_better_arm: str | None = None,
           standard_better_arm: str | None = None) -> Decision:
    """Apply the rule. All rate inputs are percentage points.

    `d` and `ci` are the paired not-found-rate difference nf(A)-nf(B), points.
    `nf_rate_a`/`nf_rate_b` route a Difference to the lower not-found arm.
    The optional *_better_arm let the caller surface a standard/pessimistic
    disagreement as the finding regardless of branch (spec §2).
    """
    lo, hi = ci
    abs_d = abs(d)
    framing_disagreement = (
        standard_better_arm is not None
        and pessimistic_better_arm is not None
        and standard_better_arm != pessimistic_better_arm)

    tripped = _guards(arm_a, arm_b, n_analysis, abs_d)

    if "intersection_loss" in tripped:
        return Decision(branch=Branch.VOID, guards_tripped=tripped,
                        route_on=None, framing_disagreement=framing_disagreement,
                        reason="analysis set below 90% of 120; no result to report")

    if tripped:
        return Decision(branch=Branch.INCONCLUSIVE, guards_tripped=tripped,
                        route_on="cost", framing_disagreement=framing_disagreement,
                        reason=f"guard tripped: {', '.join(tripped)}")

    if -DELTA < lo and hi < DELTA:
        return Decision(branch=Branch.EQUIVALENT, route_on="cost",
                        framing_disagreement=framing_disagreement,
                        reason="CI within (-delta, +delta); route on cost")

    excludes_zero = lo > 0 or hi < 0
    if excludes_zero and abs_d > DELTA:
        winner = "A" if nf_rate_a < nf_rate_b else "B"
        return Decision(branch=Branch.DIFFERENCE, route_on="quality",
                        winner_arm=winner,
                        framing_disagreement=framing_disagreement,
                        reason=f"CI excludes zero and |d|>{DELTA}; "
                               f"route to lower not-found arm {winner}")

    return Decision(branch=Branch.INCONCLUSIVE, route_on="cost",
                    framing_disagreement=framing_disagreement,
                    reason="neither equivalence nor difference resolved; "
                           "route on cost, quality comparison unresolved")
```

- [ ] **Task 4.1 Step 4: Run, expect PASS.** `pytest tests/test_coding_decision.py -v`

- [ ] **Task 4.1 Step 5: Lint and commit.**

```bash
ruff check governance/coding_decision.py
git add governance/coding_decision.py tests/test_coding_decision.py
git commit -m "feat(P2-4): pre-registered decision rule and four guards"
```

---

## Chunk 5: Price table, schema, the coding-run writer, and the NULL contract

Storage plumbing plus the price table whose absence is a terminal state. No API calls; the DB `INSERT` is exercised only at run time (like `record_structuring_run`, which has no live-DB unit test), so the testable surface is the pure row-shaping and the price arithmetic.

**Files:**
- Create: `governance/pricing.py`
- Modify: `db/schema.sql`
- Modify: `governance/evaluate.py`
- Modify: `governance/requirements.txt`
- Test: `tests/test_pricing.py`
- Test: `tests/test_evaluate.py` (extend for the coding row shape)

### Task 5.1: The price table loader

Cost is meaningless in raw tokens here: xhigh emits more reasoning tokens while being cheaper per token, so summing tokens and picking the lower routes to the wrong arm (spec §8). `governance/pricing.json` is a **user-supplied committed input**; do not invent prices. Absent → a typed "no cost winner" signal, not a crash.

- [ ] **Step 1: Write failing tests** in `tests/test_pricing.py`

```python
"""P2-4 price table. Absent table is a terminal state, not a crash (spec §8)."""
from __future__ import annotations

import json

import pytest

from governance.pricing import PriceTable, cost_usd, load_price_table


def _write(tmp_path, obj):
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_absent_table_returns_none(tmp_path):
    assert load_price_table(tmp_path / "nope.json") is None


def test_present_table_loads_with_source_and_date(tmp_path):
    p = _write(tmp_path, {
        "source": "https://example/pricing", "retrieved": "2026-07-22",
        "prices": {"claude-sonnet-5": {"input_per_mtok": 3.0,
                                       "output_per_mtok": 15.0}}})
    table = load_price_table(p)
    assert isinstance(table, PriceTable)
    assert table.source and table.retrieved


def test_malformed_table_raises_rather_than_guessing(tmp_path):
    p = _write(tmp_path, {"prices": {"claude-sonnet-5": {"input_per_mtok": 3.0}}})
    with pytest.raises(ValueError):     # missing output_per_mtok, missing source
        load_price_table(p)


def test_cost_uses_both_input_and_output_prices(tmp_path):
    p = _write(tmp_path, {
        "source": "s", "retrieved": "2026-07-22",
        "prices": {"claude-sonnet-5": {"input_per_mtok": 3.0,
                                       "output_per_mtok": 15.0}}})
    table = load_price_table(p)
    # 1,000,000 input @ $3 + 1,000,000 output @ $15 = $18.
    assert cost_usd(table, "claude-sonnet-5", 1_000_000, 1_000_000) == pytest.approx(18.0)


def test_cost_for_an_unpriced_model_raises(tmp_path):
    p = _write(tmp_path, {
        "source": "s", "retrieved": "2026-07-22",
        "prices": {"claude-sonnet-5": {"input_per_mtok": 3.0,
                                       "output_per_mtok": 15.0}}})
    table = load_price_table(p)
    with pytest.raises(KeyError):
        cost_usd(table, "claude-opus-4-8", 100, 100)
```

- [ ] **Step 2: Run, expect FAIL.** `pytest tests/test_pricing.py -v`

- [ ] **Step 3: Implement** `governance/pricing.py`:

```python
"""Per-model price table for the coding benchmark's cost comparison (spec §8).

governance/pricing.json is a USER-SUPPLIED committed input. An invented table
is the same class of error this re-scope exists to prevent, so an absent table
returns None and the benchmark declines to name a cost winner, leaving
ROUTING["coding"] unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICE_PATH = REPO_ROOT / "governance" / "pricing.json"


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


@dataclass(frozen=True)
class PriceTable:
    source: str
    retrieved: str
    prices: dict[str, ModelPrice]


def load_price_table(path: Path = DEFAULT_PRICE_PATH) -> PriceTable | None:
    """Load the table, or None if the file is absent (a terminal state).

    A present-but-malformed table raises: a bad price is worse than none,
    because it silently produces a wrong cost winner.
    """
    path = Path(path)
    if not path.is_file():
        return None

    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        source = raw["source"]
        retrieved = raw["retrieved"]
        prices = {
            model: ModelPrice(input_per_mtok=float(p["input_per_mtok"]),
                              output_per_mtok=float(p["output_per_mtok"]))
            for model, p in raw["prices"].items()
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"governance/pricing.json is malformed ({exc}). A wrong price routes "
            f"production to the wrong configuration; refusing to guess.") from exc

    if not source or not retrieved or not prices:
        raise ValueError("pricing.json must carry source, retrieved, and prices")
    return PriceTable(source=source, retrieved=retrieved, prices=prices)


def cost_usd(table: PriceTable, model: str,
             input_tokens: int, output_tokens: int) -> float:
    """Cost in USD for a model at a token count. Keyed on the REQUESTED alias
    (claude-sonnet-5, claude-opus-4-8), which is how a plan prices a family."""
    price = table.prices[model]        # KeyError on an unpriced model, loudly
    return (input_tokens / 1e6 * price.input_per_mtok
            + output_tokens / 1e6 * price.output_per_mtok)
```

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_pricing.py -v`

- [ ] **Step 5: Commit.**

```bash
ruff check governance/pricing.py
git add governance/pricing.py tests/test_pricing.py
git commit -m "feat(P2-4): price table loader; absent table is a terminal state"
```

### Task 5.2: Schema migration

- [ ] **Step 1: Append to `db/schema.sql`** (idempotent, after the `eval_runs` table + its index):

```sql
-- P2-4: the coding routing benchmark stores a JSONB metrics blob (per-arm
-- section-1 metrics plus the comparison block) and the effort half of the
-- configuration under test. accuracy/f1/precision/recall stay NULL for coding
-- rows: no held-out set carries gold billing codes, so a verified rate must
-- not be written where a Phase 3 dashboard would read it as accuracy.
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS metrics JSONB;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS model_effort TEXT;
```

- [ ] **Step 2: Verify idempotency by inspection.** `ADD COLUMN IF NOT EXISTS` is safe to re-run; `make db-init` pipes the whole file each time. No test (DDL against a live DB is out of unit scope).

- [ ] **Step 3: Commit.**

```bash
git add db/schema.sql
git commit -m "feat(P2-4): add eval_runs.metrics JSONB and model_effort columns"
```

### Task 5.3: `record_coding_run` and the all-NULL accuracy contract

One `eval_runs` row per arm, each self-describing (the comparison block is duplicated into both rows' `metrics`, since Phase 3 reads this table and a single row cannot hold two models). `accuracy`, `f1`, `precision`, `recall` are NULL; the verified rate lives only in `metrics`. `record_structuring_run` stays untouched (it passes `metrics["f1"]` unconditionally and would `KeyError` on a coding payload — spec §6).

**Phase 3 note, verified 2026-07-22:** there are currently **no** `SELECT` readers of `eval_runs` anywhere in the repo (`drift.py` consumes DataFrames, `transparency.py` reads `model_inventory`). So "verify Phase 3 consumers tolerate an all-NULL accuracy family" (spec §6) resolves to **pinning the contract now** so P3-1/P3-2 must honor it when they are built. This task adds that pin as a test and a ROADMAP note; it does not need to modify a consumer, because none exists yet.

- [ ] **Step 1: Write failing tests** (append to `tests/test_evaluate.py`)

```python
from governance.evaluate import coding_row_params


def test_coding_row_leaves_the_accuracy_family_null():
    # The whole point: a coding row must not write a verified rate into a column
    # a Phase 3 dashboard reads as accuracy. All four stay NULL; metrics carries
    # the real numbers (spec §6).
    params = coding_row_params(
        agent_name="coding", model="claude-sonnet-5", model_effort="xhigh",
        window_label="v1", dataset_ref="aci-bench-heldout-v1",
        n_examples=118, metrics={"verified_rate": 94.0})
    # (agent_name, model, model_effort, window_label, dataset_ref, n_examples,
    #  accuracy, f1, precision, recall, metrics_json)
    assert params[6] is None and params[7] is None
    assert params[8] is None and params[9] is None
    assert params[2] == "xhigh"
    import json
    assert json.loads(params[10])["verified_rate"] == 94.0


def test_coding_row_carries_n_examples_as_the_intersection():
    params = coding_row_params(
        agent_name="coding", model="m", model_effort="high", window_label="v1",
        dataset_ref="d", n_examples=110, metrics={})
    assert params[5] == 110
```

- [ ] **Step 2: Run, expect FAIL** (`coding_row_params` undefined). `pytest tests/test_evaluate.py -k coding -v`

- [ ] **Step 3: Implement** in `governance/evaluate.py`. Add a pure row-shaper and the writer that uses it:

```python
import json


def coding_row_params(*, agent_name: str, model: str, model_effort: str,
                      window_label: str, dataset_ref: str, n_examples: int,
                      metrics: dict) -> tuple:
    """Build the eval_runs row for one coding-benchmark arm.

    accuracy/f1/precision/recall are NULL by construction: no held-out set
    carries gold codes, so the verified rate lives only in the metrics JSONB
    (spec §6). Pure and DB-free, so the NULL contract is unit-testable.
    """
    return (agent_name, model, model_effort, window_label, dataset_ref,
            n_examples, None, None, None, None, json.dumps(metrics))


def record_coding_run(*, agent_name: str, model: str, model_effort: str,
                      window_label: str, dataset_ref: str, n_examples: int,
                      metrics: dict) -> int:
    """Write one coding-benchmark arm to eval_runs. Returns the row id."""
    params = coding_row_params(
        agent_name=agent_name, model=model, model_effort=model_effort,
        window_label=window_label, dataset_ref=dataset_ref,
        n_examples=n_examples, metrics=metrics)
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO eval_runs (agent_name, model, model_effort, "
            "window_label, dataset_ref, n_examples, accuracy, f1, precision, "
            "recall, metrics) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            params,
        ).fetchone()
        return row[0]
```

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_evaluate.py -v`

- [ ] **Step 5: Add the ROADMAP contract note.** In `docs/ROADMAP.md`, under P2-4 (or the Phase 3 preamble), add:

> P2-4 writes coding `eval_runs` rows with `accuracy`/`f1`/`precision`/`recall` NULL and the verified rate in `metrics` JSONB. Any Phase 3 consumer (P3-1 runner, P3-2 windows, P3-5 API, P4-1 dashboard) MUST tolerate an all-NULL accuracy family and read coding numbers from `metrics`. As of 2026-07-22 there are no `eval_runs` SELECT readers, so this is a forward contract, not a migration.

- [ ] **Step 6: Add explicit numpy/scipy pins** to `governance/requirements.txt` (they are a direct dependency now, not just transitive via scikit-learn):

```
numpy==2.0.2
scipy==1.15.3
```

- [ ] **Step 7: Commit.**

```bash
ruff check governance/evaluate.py
git add governance/evaluate.py tests/test_evaluate.py docs/ROADMAP.md governance/requirements.txt
git commit -m "feat(P2-4): record_coding_run with all-NULL accuracy contract; pin numpy/scipy"
```

---

## Chunk 6: Data and execution

Input construction, stratification, the train loader for the pilot, and the per-arm cached execution with its failure and intersection policy. This chunk's per-note execution touches the API at run time only; all tests fake `call_detailed`/`parse_and_enrich` and spend nothing.

**Files:**
- Modify: `shared/llm.py` (add `LLMResult.to_json`/`from_json`)
- Create: `governance/coding_benchmark.py` (input construction, execution, intersection)
- Create: `governance/coding_pilot.py` (train loader; pilot report lands in Chunk 7)
- Test: `tests/test_llm_call_detailed.py` (extend), `tests/test_coding_benchmark.py`, `tests/test_coding_pilot.py`

### Task 6.1: `LLMResult` serialization for the cache

The cache must store a serialized `LLMResult`, not bare text: on a hit `call_detailed` never runs, so a bare-string cache loses `observed_model` and token counts for every completed note, and a resumed run would be missing the cost inputs to the Equivalent branch for exactly the notes already paid for (spec §8). Latency is the one field that does **not** survive a hit; it is wall-clock, not a property of the response.

- [ ] **Step 1: Write failing tests** (append to `tests/test_llm_call_detailed.py`)

```python
def test_llmresult_json_roundtrip_preserves_model_and_tokens():
    r = LLMResult(text="hi", model="claude-opus-4-8-20260101",
                  input_tokens=7, output_tokens=9, stop_reason="end_turn")
    back = LLMResult.from_json(r.to_json())
    assert back == r
    assert back.model == "claude-opus-4-8-20260101"
    assert back.input_tokens == 7 and back.output_tokens == 9


def test_observed_model_and_tokens_survive_a_cache_roundtrip(tmp_path):
    from governance.llm_cache import Cache
    cache = Cache(tmp_path)
    r = LLMResult(text="t", model="claude-sonnet-5-20260101",
                  input_tokens=3, output_tokens=4, stop_reason="end_turn")
    cache.put("k", r.to_json())
    back = LLMResult.from_json(cache.get("k"))
    assert back.model == "claude-sonnet-5-20260101"
    assert (back.input_tokens, back.output_tokens) == (3, 4)
```

- [ ] **Step 2: Run, expect FAIL.** `pytest tests/test_llm_call_detailed.py -k roundtrip -v`

- [ ] **Step 3: Implement** — add methods to `LLMResult` in `shared/llm.py`:

```python
    def to_json(self) -> str:
        return json.dumps({
            "text": self.text, "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "stop_reason": self.stop_reason,
        })

    @classmethod
    def from_json(cls, s: str) -> "LLMResult":
        d = json.loads(s)
        return cls(text=d["text"], model=d["model"],
                   input_tokens=d["input_tokens"],
                   output_tokens=d["output_tokens"],
                   stop_reason=d["stop_reason"])
```

(`json` is already imported at the top of `shared/llm.py`.)

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_llm_call_detailed.py -v`

- [ ] **Step 5: Commit.**

```bash
git add shared/llm.py tests/test_llm_call_detailed.py
git commit -m "feat(P2-4): LLMResult JSON serialization so cache hits keep model+tokens"
```

### Task 6.2: Input construction and stratification

Build each `SoapNote` from the ACI reference note by concatenating the bodies of all sections sharing a `primary` bucket, joined with `"\n\n"` (spec §3). Stratify on `plan == ""` (27 empty / 93 non-empty, re-measured 2026-07-22). Both arms receive byte-identical input.

- [ ] **Step 1: Write failing tests** in `tests/test_coding_benchmark.py`

```python
"""P2-4 benchmark orchestration. Execution is faked; nothing here spends."""
from __future__ import annotations

import pytest

from governance.coding_benchmark import build_soap_from_reference, plan_is_empty

FUSED_ONLY = (
    "CHIEF COMPLAINT\r\n\r\nCough.\r\n\r\n"
    "ASSESSMENT AND PLAN\r\n\r\nURI. Rest.\r\n"
)
SEPARATE_PLAN = (
    "CHIEF COMPLAINT\r\n\r\nCough.\r\n\r\n"
    "ASSESSMENT AND PLAN\r\n\r\nURI.\r\n\r\n"
    "PLAN\r\n\r\nRest and fluids.\r\n"
)


def test_soap_concatenates_primary_bucket_bodies():
    soap = build_soap_from_reference(FUSED_ONLY)
    assert soap.subjective == "Cough."
    assert soap.assessment == "URI. Rest."     # fused section -> assessment
    assert soap.plan == ""                       # nothing maps to plan


def test_fused_only_note_has_empty_plan():
    assert plan_is_empty(build_soap_from_reference(FUSED_ONLY)) is True


def test_fused_note_with_a_separate_plan_header_is_not_empty_plan():
    # This is the 24-note case the stratification exists to separate from the 27.
    soap = build_soap_from_reference(SEPARATE_PLAN)
    assert soap.plan == "Rest and fluids."
    assert plan_is_empty(soap) is False


def test_both_arms_receive_byte_identical_input():
    a = build_soap_from_reference(FUSED_ONLY)
    b = build_soap_from_reference(FUSED_ONLY)
    assert a.model_dump_json() == b.model_dump_json()
```

- [ ] **Step 2: Run, expect FAIL** (module missing). `pytest tests/test_coding_benchmark.py -v`

- [ ] **Step 3: Begin** `governance/coding_benchmark.py` with input construction:

```python
"""P2-4 coding routing benchmark orchestration.

Builds byte-identical SoapNote inputs from ACI reference notes, runs both arms
through the shared parse path with per-arm (model, effort) overrides, caches a
serialized LLMResult per call, and forms the analysis set as the intersection of
notes both arms parsed. Touches the API and DB only at run time; every function
here is unit-tested with fakes.

Never calls agent.run(): that logs to agent_decisions with int encounter ids and
NOT NULL foreign keys, and ACI ids are strings like D2N068 (spec §7).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from governance.aci_sections import SOAP_BUCKETS, bucket_sections
from governance.llm_cache import Cache, cache_key
from governance.structuring_eval import hash_prompt
from services.agent_coding.agent import (
    CodingError, _MAX_TOKENS as CODING_MAX_TOKENS, _SYSTEM as CODING_SYSTEM,
    parse_and_enrich,
)
from shared.llm import LLMResult, TruncatedResponseError, call_detailed
from shared.schemas import SoapNote


def build_soap_from_reference(reference_note: str) -> SoapNote:
    """Concatenate the bodies of all sections sharing a `primary` bucket,
    joined with two newlines (spec §3). Fused ASSESSMENT AND PLAN reports as
    assessment via RefSection.primary, which is why a fused-only note has an
    empty plan."""
    by_bucket: dict[str, list[str]] = {b: [] for b in SOAP_BUCKETS}
    for section in bucket_sections(reference_note):
        by_bucket[section.primary].append(section.body)
    return SoapNote(**{b: "\n\n".join(by_bucket[b]) for b in SOAP_BUCKETS})


def plan_is_empty(soap: SoapNote) -> bool:
    """The stratification variable (spec §3): 27 empty, 93 non-empty."""
    return soap.plan == ""
```

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_coding_benchmark.py -v`

- [ ] **Step 5: Commit.**

```bash
ruff check governance/coding_benchmark.py
git add governance/coding_benchmark.py tests/test_coding_benchmark.py
git commit -m "feat(P2-4): SoapNote construction from reference notes; plan-empty stratum"
```

### Task 6.3: The ACI train loader for the pilot

The pilot draws from the **train** split, never held-out (spec §8). `governance/heldout.py` deliberately exposes no train path, so this is a small separate loader, not a change to that module.

- [ ] **Step 1: Write failing tests** in `tests/test_coding_pilot.py`

```python
"""P2-4 pilot: train-split loader and diagnostics. No held-out data (spec §8)."""
from __future__ import annotations

from governance.coding_pilot import load_aci_train, pin_pilot_ids


def test_train_loader_returns_only_train_split_encounters():
    train = load_aci_train()
    assert len(train) == 67          # re-measured 2026-07-22
    ids = {e.encounter_id for e in train}
    # No held-out id may appear (the pilot must never touch the analysis set).
    from governance.heldout import load_aci_heldout
    heldout_ids = {e.encounter_id for e in load_aci_heldout()}
    assert ids.isdisjoint(heldout_ids)


def test_pilot_draw_is_pinned_and_reproducible():
    a = pin_pilot_ids(n=5, seed=20260722)
    b = pin_pilot_ids(n=5, seed=20260722)
    assert a == b and len(a) == 5    # cannot be redrawn until it gives a nicer answer
```

- [ ] **Step 2: Run, expect FAIL.** `pytest tests/test_coding_pilot.py -v`

- [ ] **Step 3: Implement** `governance/coding_pilot.py` (loader + pinned draw; the report lands in Chunk 7):

```python
"""P2-4 pilot. Drawn from the ACI train split, never the held-out set: proceeding
after seeing held-out outcomes would be optional stopping inside the analysis set
(spec §8). heldout.py exposes no train path on purpose, so the loader lives here.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from governance.heldout import DEFAULT_DATA_ROOT
from shared.schemas import SoapNote  # noqa: F401  (used by Chunk 7 report)
from shared.splits import TRAIN, build_all_records

REPO_ROOT = Path(__file__).resolve().parents[1]


class TrainExample:
    __slots__ = ("encounter_id", "reference_note")

    def __init__(self, encounter_id: str, reference_note: str):
        self.encounter_id = encounter_id
        self.reference_note = reference_note


def load_aci_train(data_root: Path = DEFAULT_DATA_ROOT) -> list[TrainExample]:
    """The ACI train-split encounters, reference note only. No split verification
    guard here (that guards the held-out set); this reads train rows the pilot is
    allowed to see."""
    records = build_all_records(data_root)
    wanted = {r.encounter_id for r in records
              if r.dataset == "aci-bench" and r.split == TRAIN}

    challenge = data_root / "aci-bench" / "data" / "challenge_data"
    out: list[TrainExample] = []
    seen: set[str] = set()
    for csv_path in sorted(challenge.glob("*.csv")):
        if csv_path.name.endswith("_metadata.csv"):
            continue
        with csv_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                eid = row["encounter_id"]
                if eid in wanted and eid not in seen:
                    seen.add(eid)
                    out.append(TrainExample(eid, row["note"]))
    out.sort(key=lambda e: e.encounter_id)
    return out


def pin_pilot_ids(n: int, seed: int,
                  data_root: Path = DEFAULT_DATA_ROOT) -> list[str]:
    """A deterministic, recorded draw of n train ids, so the pilot cannot be
    redrawn until it gives a preferred answer (spec §8)."""
    ids = [e.encounter_id for e in load_aci_train(data_root)]
    # sha256-ranked, seed-salted: stable across processes and OSes.
    ranked = sorted(ids, key=lambda i: hashlib.sha256(
        f"{seed}:{i}".encode("utf-8")).hexdigest())
    return sorted(ranked[:n])
```

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_coding_pilot.py -v`

- [ ] **Step 5: Commit.**

```bash
ruff check governance/coding_pilot.py
git add governance/coding_pilot.py tests/test_coding_pilot.py
git commit -m "feat(P2-4): ACI train loader and pinned pilot draw"
```

### Task 6.4: Per-arm cached execution with failure handling

Run one arm on one note: cache key folds effort, prompt hash, and max_tokens (spec §8); a cache hit reconstructs the `LLMResult` and marks latency absent; a truncation or parse failure returns a typed failure, never aborts; `observed_model` is checked by resolved family and a mismatch fails the run (spec §6).

- [ ] **Step 1: Write failing tests** (append to `tests/test_coding_benchmark.py`)

```python
import governance.coding_benchmark as cb
from governance.llm_cache import Cache
from shared.llm import LLMResult
from shared.schemas import SoapNote

SOAP = SoapNote(subjective="s", objective="o", assessment="a", plan="p")


def _fake_detailed(monkeypatch, result_or_exc, calls=None):
    def fake(component, system, user, max_tokens, model=None, effort=None):
        if calls is not None:
            calls.append((model, effort))
        if isinstance(result_or_exc, Exception):
            raise result_or_exc
        return result_or_exc
    monkeypatch.setattr(cb, "call_detailed", fake)


def _ok(model="claude-sonnet-5-20260101", text='{"codes": [], "confidence": 0.5}'):
    return LLMResult(text=text, model=model, input_tokens=10,
                     output_tokens=20, stop_reason="end_turn")


def test_run_arm_passes_the_override_and_returns_output(monkeypatch, tmp_path):
    calls = []
    _fake_detailed(monkeypatch, _ok(), calls)
    r = cb.run_arm_on_note(SOAP, model="claude-sonnet-5", effort="xhigh",
                           cache=Cache(tmp_path))
    assert calls == [("claude-sonnet-5", "xhigh")]     # override reached the call
    assert r.failure is None and r.output is not None
    assert r.observed_model == "claude-sonnet-5-20260101"
    assert r.latency_ms is not None                     # cold call has latency


def test_cache_hit_reconstructs_llmresult_and_drops_latency(monkeypatch, tmp_path):
    cache = Cache(tmp_path)
    _fake_detailed(monkeypatch, _ok())
    cb.run_arm_on_note(SOAP, model="claude-sonnet-5", effort="xhigh", cache=cache)
    # Second call must not hit the API: make the fake explode if it runs.
    _fake_detailed(monkeypatch, RuntimeError("must not call API on a hit"))
    r = cb.run_arm_on_note(SOAP, model="claude-sonnet-5", effort="xhigh", cache=cache)
    assert r.output is not None
    assert r.observed_model == "claude-sonnet-5-20260101"  # survived the hit
    assert r.latency_ms is None                            # latency does not


def test_truncation_is_a_typed_failure_not_an_abort(monkeypatch, tmp_path):
    from shared.llm import TruncatedResponseError
    _fake_detailed(monkeypatch, TruncatedResponseError("coding", 5000))
    r = cb.run_arm_on_note(SOAP, model="claude-opus-4-8", effort="high",
                           cache=Cache(tmp_path))
    assert r.output is None and "truncat" in r.failure.lower()


def test_parse_failure_is_typed_and_keeps_the_llmresult(monkeypatch, tmp_path):
    _fake_detailed(monkeypatch, _ok(text="not json"))
    r = cb.run_arm_on_note(SOAP, model="claude-sonnet-5", effort="xhigh",
                           cache=Cache(tmp_path))
    assert r.output is None and r.failure is not None
    assert r.tokens == (10, 20)         # tokens still captured for cost accounting


def test_observed_model_family_mismatch_fails_the_run(monkeypatch, tmp_path):
    _fake_detailed(monkeypatch, _ok(model="claude-opus-4-8-20260101"))
    with pytest.raises(ValueError, match="observed model"):
        cb.run_arm_on_note(SOAP, model="claude-sonnet-5", effort="xhigh",
                           cache=Cache(tmp_path))
```

- [ ] **Step 2: Run, expect FAIL.** `pytest tests/test_coding_benchmark.py -k "run_arm or cache_hit or truncation or parse_failure or observed_model" -v`

- [ ] **Step 3: Implement** (append to `governance/coding_benchmark.py`):

```python
_CODING_VERSION = f"{{effort}}|{hash_prompt(CODING_SYSTEM)}|max{CODING_MAX_TOKENS}"


def _cache_key(model: str, effort: str, payload: str) -> str:
    version = f"{effort}|{hash_prompt(CODING_SYSTEM)}|max{CODING_MAX_TOKENS}"
    return cache_key("coding", model, version, payload)


@dataclass(frozen=True)
class ArmNoteResult:
    output: object | None            # CodingOutput or None on failure
    observed_model: str | None
    tokens: tuple[int, int] | None   # (input, output)
    latency_ms: int | None           # None on a cache hit (wall-clock only)
    failure: str | None


def run_arm_on_note(soap: SoapNote, model: str, effort: str,
                    cache: Cache) -> ArmNoteResult:
    """Run one arm on one note, cached. A truncation or parse failure is typed,
    not raised (spec §8); an observed-model family mismatch IS raised (spec §6)."""
    payload = soap.model_dump_json()
    key = _cache_key(model, effort, payload)

    cached = cache.get(key)
    if cached is not None:
        result = LLMResult.from_json(cached)
        latency_ms = None
    else:
        started = time.perf_counter()
        try:
            result = call_detailed("coding", system=CODING_SYSTEM, user=payload,
                                   max_tokens=CODING_MAX_TOKENS,
                                   model=model, effort=effort)
        except TruncatedResponseError as exc:
            return ArmNoteResult(None, None, None, None, f"truncated: {exc}")
        latency_ms = int((time.perf_counter() - started) * 1000)
        cache.put(key, result.to_json())

    # Resolved-family match: ROUTING holds a bare alias, the API echoes a
    # snapshot id, so exact equality would hard-fail on note 1 (spec §6).
    if not result.model.startswith(model):
        raise ValueError(
            f"observed model {result.model!r} does not match requested "
            f"{model!r}; refusing to attribute this note's result to an arm")

    tokens = (result.input_tokens, result.output_tokens)
    try:
        output = parse_and_enrich(result.text)
    except CodingError as exc:
        return ArmNoteResult(None, result.model, tokens, latency_ms,
                             f"parse: {exc}")
    return ArmNoteResult(output, result.model, tokens, latency_ms, None)
```

(Delete the stray `_CODING_VERSION` line if the executor prefers; it is illustrative of the version string and unused. Keep only `_cache_key`.)

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_coding_benchmark.py -v`

- [ ] **Step 5: Commit.**

```bash
ruff check governance/coding_benchmark.py
git add governance/coding_benchmark.py tests/test_coding_benchmark.py
git commit -m "feat(P2-4): per-arm cached execution with typed failures and observed-model check"
```

### Task 6.5: The analysis set (intersection) and attrition

The analysis set is the notes **both** arms parsed, so the paired bootstrap sees the same notes. The void threshold is on the intersection, not per arm: two arms failing 8% on disjoint notes would drop 16% without tripping any per-arm threshold (spec §8). Attrition is non-random, so record the reference-note length distribution of dropped vs retained notes.

- [ ] **Step 1: Write failing tests** (append to `tests/test_coding_benchmark.py`)

```python
from governance.coding_benchmark import build_analysis_set, INTERSECTION_MIN


def test_analysis_set_is_the_intersection_of_parsed_notes():
    # note -> (arm_a_ok, arm_b_ok)
    per_note = {
        "D2N001": (True, True),
        "D2N002": (True, False),   # arm B failed
        "D2N003": (False, True),   # arm A failed
        "D2N004": (True, True),
    }
    a = build_analysis_set(per_note)
    assert a.ids == ["D2N001", "D2N004"]
    assert a.dropped_ids == ["D2N002", "D2N003"]


def test_void_threshold_is_108_of_120():
    assert INTERSECTION_MIN == 108     # 0.90 * 120


def test_intersection_below_the_floor_is_flagged_void():
    per_note = {f"n{i}": (True, i >= 20) for i in range(120)}  # 20 arm-B fails
    a = build_analysis_set(per_note)
    assert len(a.ids) == 100 and a.is_void is True


def test_intersection_at_the_floor_is_not_void():
    per_note = {f"n{i}": (True, i >= 12) for i in range(120)}  # 108 retained
    a = build_analysis_set(per_note)
    assert len(a.ids) == 108 and a.is_void is False
```

- [ ] **Step 2: Run, expect FAIL.** `pytest tests/test_coding_benchmark.py -k "analysis_set or void or intersection" -v`

- [ ] **Step 3: Implement** (append to `governance/coding_benchmark.py`):

```python
INTERSECTION_MIN = 108        # 0.90 * 120 target notes (spec §2, §8)


@dataclass(frozen=True)
class AnalysisSet:
    ids: list[str]            # notes both arms parsed, sorted
    dropped_ids: list[str]    # notes at least one arm failed, sorted

    @property
    def is_void(self) -> bool:
        return len(self.ids) < INTERSECTION_MIN


def build_analysis_set(per_note_ok: dict[str, tuple[bool, bool]]) -> AnalysisSet:
    """Intersection of notes both arms parsed. Void is judged on the
    intersection size, never per arm (spec §8)."""
    keep, drop = [], []
    for eid, (a_ok, b_ok) in per_note_ok.items():
        (keep if a_ok and b_ok else drop).append(eid)
    return AnalysisSet(ids=sorted(keep), dropped_ids=sorted(drop))


def attrition_length_summary(dropped_lengths: list[int],
                             retained_lengths: list[int]) -> dict:
    """Reference-note length distribution of dropped vs retained notes, so the
    SHAPE of a non-random loss is visible, not merely bounded (spec §8)."""
    def _summ(xs: list[int]) -> dict:
        if not xs:
            return {"n": 0, "min": None, "median": None, "max": None, "mean": None}
        s = sorted(xs)
        mid = len(s) // 2
        median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
        return {"n": len(s), "min": s[0], "median": median, "max": s[-1],
                "mean": sum(s) / len(s)}
    return {"dropped": _summ(dropped_lengths), "retained": _summ(retained_lengths)}
```

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_coding_benchmark.py -v`

- [ ] **Step 5: Commit.**

```bash
ruff check governance/coding_benchmark.py
git add governance/coding_benchmark.py tests/test_coding_benchmark.py
git commit -m "feat(P2-4): intersection analysis set, void threshold, attrition summary"
```

---

## Chunk 7: Artifact, replay, pilot diagnostics, and the CLI

The committed artifact carries **counts only, no billing codes**: a per-encounter diagnosis code is clinical data (spec §5e), so codes and model prose go to a gitignored `.full.json` roster and the committed artifact holds per-note verdict *tallies* the rates recompute from. Replay recomputes every rate from those tallies and hard-errors on a `vocab_version` mismatch; cost and latency are measured-once and verified against stored per-note values, not re-derived (spec §6).

**Files:**
- Modify: `governance/coding_benchmark.py` (per-note tally, aggregation shared by build+replay, artifact, replay)
- Modify: `governance/coding_pilot.py` (pilot diagnostics)
- Create: `scripts/run_coding_benchmark.py`
- Modify: `Makefile`
- Test: `tests/test_coding_benchmark.py`, `tests/test_coding_pilot.py`

### The committed per-note record (no billing codes)

Per arm, per analysis-set note: `{verified, not_found, unchecked, cause1..4, input_tokens, output_tokens, latency_ms}` plus the note's `plan_empty` stratum flag and the between-arm `agreement` value (stored once per note). The dedup/floor keys — the actual codes — never enter this file; they live in the roster.

### Task 7.1: Per-note tally, shared aggregation, artifact, replay

- [ ] **Step 1: Write failing tests** (append to `tests/test_coding_benchmark.py`)

```python
import json
from governance.coding_benchmark import (
    NoteTally, aggregate_tallies, build_committed_artifact, build_roster,
    replay_coding,
)
from shared import vocab


def _tally(v, nf, un, c1=0, c2=0, c3=0, c4=0, itok=10, otok=20, lat=100):
    return NoteTally(verified=v, not_found=nf, unchecked=un,
                     cause1=c1, cause2=c2, cause3=c3, cause4=c4,
                     input_tokens=itok, output_tokens=otok, latency_ms=lat)


def test_aggregate_tallies_matches_arm_summary_rates():
    tallies = [_tally(1, 1, 0), _tally(2, 0, 0)]     # pooled 3/4 verified
    agg = aggregate_tallies(tallies)
    assert agg["verified_rate"] == pytest.approx(75.0)
    assert agg["not_found_rate"] == pytest.approx(25.0)


def test_committed_artifact_carries_no_billing_codes(tmp_path):
    # Build a tiny run payload with a real code in the roster; assert the code
    # never appears in the committed artifact.
    committed = build_committed_artifact(
        arm_tallies={"A": {"D2N001": _tally(1, 1, 0, c1=1)},
                     "B": {"D2N001": _tally(2, 0, 0)}},
        agreement={"D2N001": 0.5}, strata={"D2N001": False},
        comparison={"branch_fired": "inconclusive"},
        run_meta={"vocab_version": vocab.VOCAB_VERSION,
                  "vocab_floor_version": "none", "price_table_ref": None,
                  "split_digest": "d" * 64, "dataset_ref": "aci-bench-heldout-v1"},
        arm_meta={"A": {"requested_model": "claude-sonnet-5",
                        "requested_effort": "xhigh",
                        "observed_model": "claude-sonnet-5-20260101"},
                  "B": {"requested_model": "claude-opus-4-8",
                        "requested_effort": "high",
                        "observed_model": "claude-opus-4-8-20260101"}})
    blob = json.dumps(committed)
    assert "E11.9" not in blob and "E119" not in blob   # no codes leak


def test_replay_recomputes_rates_and_matches(tmp_path):
    committed = build_committed_artifact(
        arm_tallies={"A": {"n1": _tally(3, 1, 0, c1=1)},
                     "B": {"n1": _tally(4, 0, 0)}},
        agreement={"n1": 0.8}, strata={"n1": False},
        comparison={"branch_fired": "inconclusive"},
        run_meta={"vocab_version": vocab.VOCAB_VERSION,
                  "vocab_floor_version": "none", "price_table_ref": None,
                  "split_digest": "d" * 64, "dataset_ref": "aci-bench-heldout-v1"},
        arm_meta={"A": {"requested_model": "m", "requested_effort": "xhigh",
                        "observed_model": "m-1"},
                  "B": {"requested_model": "m2", "requested_effort": "high",
                        "observed_model": "m2-1"}})
    path = tmp_path / "coding_run.json"
    path.write_text(json.dumps(committed), encoding="utf-8")
    out = replay_coding(path)
    assert out["A"]["verified_rate"] == pytest.approx(75.0)


def test_replay_hard_errors_on_vocab_version_mismatch(tmp_path):
    committed = build_committed_artifact(
        arm_tallies={"A": {"n1": _tally(3, 1, 0)}, "B": {"n1": _tally(4, 0, 0)}},
        agreement={"n1": 1.0}, strata={"n1": False},
        comparison={}, run_meta={"vocab_version": "STALE PIN",
                                 "vocab_floor_version": "none",
                                 "price_table_ref": None, "split_digest": "d" * 64,
                                 "dataset_ref": "x"},
        arm_meta={"A": {"requested_model": "m", "requested_effort": "x",
                        "observed_model": "m"},
                  "B": {"requested_model": "m2", "requested_effort": "h",
                        "observed_model": "m2"}})
    path = tmp_path / "c.json"
    path.write_text(json.dumps(committed), encoding="utf-8")
    with pytest.raises(ValueError, match="vocab_version"):
        replay_coding(path)


def test_replay_hard_errors_on_a_tampered_stored_rate(tmp_path):
    committed = build_committed_artifact(
        arm_tallies={"A": {"n1": _tally(3, 1, 0)}, "B": {"n1": _tally(4, 0, 0)}},
        agreement={"n1": 1.0}, strata={"n1": False}, comparison={},
        run_meta={"vocab_version": vocab.VOCAB_VERSION,
                  "vocab_floor_version": "none", "price_table_ref": None,
                  "split_digest": "d" * 64, "dataset_ref": "x"},
        arm_meta={"A": {"requested_model": "m", "requested_effort": "x",
                        "observed_model": "m"},
                  "B": {"requested_model": "m2", "requested_effort": "h",
                        "observed_model": "m2"}})
    committed["arms"]["A"]["verified_rate"] = 99.0     # tamper
    path = tmp_path / "c.json"
    path.write_text(json.dumps(committed), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match|recompute"):
        replay_coding(path)
```

- [ ] **Step 2: Run, expect FAIL.** `pytest tests/test_coding_benchmark.py -k "aggregate_tallies or committed or replay" -v`

- [ ] **Step 3: Implement** (append to `governance/coding_benchmark.py`). The aggregation helper is shared by build and replay so a rate cannot drift between them:

```python
import json
from pathlib import Path

from shared import vocab

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = REPO_ROOT / "governance" / "eval_artifacts"


@dataclass(frozen=True)
class NoteTally:
    verified: int
    not_found: int
    unchecked: int
    cause1: int
    cause2: int
    cause3: int
    cause4: int
    input_tokens: int
    output_tokens: int
    latency_ms: int | None

    def as_dict(self) -> dict:
        return dict(verified=self.verified, not_found=self.not_found,
                    unchecked=self.unchecked, cause1=self.cause1,
                    cause2=self.cause2, cause3=self.cause3, cause4=self.cause4,
                    input_tokens=self.input_tokens,
                    output_tokens=self.output_tokens, latency_ms=self.latency_ms)


def _rates_from_sums(v: int, nf: int, un: int,
                     c2: int, c3: int, c4: int) -> dict:
    """Pooled rates in points from summed counts. The single arithmetic both
    build and replay use, so a stored rate always recomputes from its tallies."""
    checkable = v + nf
    total = v + nf + un
    vr = None if checkable == 0 else 100.0 * v / checkable
    return {
        "verified_rate": vr,
        "not_found_rate": None if vr is None else 100.0 - vr,
        "pessimistic_verified_rate": None if total == 0 else 100.0 * v / total,
        "unchecked_share": None if total == 0 else 100.0 * un / total,
        "floor_lower": 0.0 if checkable == 0 else 100.0 * (c4 + c3 + c2) / checkable,
        "floor_upper": 0.0 if checkable == 0 else 100.0 * nf / checkable,
        "n_verified": v, "n_not_found": nf, "n_unchecked": un,
        "n_checkable": checkable, "n_codes_deduped": total,
        "floor_cause_counts": {"cause1": (nf - c2 - c3 - c4),
                               "cause2": c2, "cause3": c3, "cause4": c4},
    }


def aggregate_tallies(tallies: list[NoteTally]) -> dict:
    v = sum(t.verified for t in tallies)
    nf = sum(t.not_found for t in tallies)
    un = sum(t.unchecked for t in tallies)
    c2 = sum(t.cause2 for t in tallies)
    c3 = sum(t.cause3 for t in tallies)
    c4 = sum(t.cause4 for t in tallies)
    agg = _rates_from_sums(v, nf, un, c2, c3, c4)
    agg["n_notes"] = len(tallies)
    agg["codes_per_note"] = (agg["n_codes_deduped"] / len(tallies)
                             if tallies else 0.0)
    agg["input_tokens"] = sum(t.input_tokens for t in tallies)
    agg["output_tokens"] = sum(t.output_tokens for t in tallies)
    lat = [t.latency_ms for t in tallies if t.latency_ms is not None]
    agg["latency_notes_contributing"] = len(lat)
    agg["latency_p50"] = _pctl(lat, 50)
    agg["latency_p95"] = _pctl(lat, 95)
    return agg


def _pctl(xs: list[int], p: int) -> float | None:
    if not xs:
        return None
    import numpy as np
    return float(np.percentile(np.array(xs, float), p))


def build_committed_artifact(*, arm_tallies: dict[str, dict[str, NoteTally]],
                             agreement: dict[str, float | None],
                             strata: dict[str, bool], comparison: dict,
                             run_meta: dict, arm_meta: dict) -> dict:
    """The committed, code-free artifact. Per-note tallies + per-arm aggregates
    + the top-level comparison block (spec §6)."""
    arms: dict[str, dict] = {}
    for arm, per_note in arm_tallies.items():
        tallies = list(per_note.values())
        agg = aggregate_tallies(tallies)
        # per-stratum aggregates keyed on plan_empty (spec §3)
        strat: dict[str, dict] = {}
        for label, want in (("plan_empty", True), ("plan_nonempty", False)):
            sub = [t for eid, t in per_note.items() if strata.get(eid) is want]
            strat[label] = aggregate_tallies(sub) if sub else None
        arms[arm] = {
            **arm_meta[arm], **agg,
            "prompt_version": _cache_version_string(arm_meta[arm]["requested_effort"]),
            "max_tokens": CODING_MAX_TOKENS,
            "per_stratum": strat,
            "notes": {eid: t.as_dict() for eid, t in per_note.items()},
        }
    return {
        "created_at": _utcnow(),
        **run_meta,
        "agreement": agreement,
        "strata": strata,
        "comparison": comparison,
        "arms": arms,
    }


def _cache_version_string(effort: str) -> str:
    return f"{effort}|{hash_prompt(CODING_SYSTEM)}|max{CODING_MAX_TOKENS}"


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def build_roster(rows: list[dict]) -> dict:
    """The gitignored .full.json roster: encounter id, arm, systems seen, code,
    model_description, auto-classified cause, blank adjudication column
    (spec §5e). rows are built by the caller from the deduped codes."""
    return {"created_at": _utcnow(),
            "columns": ["encounter_id", "arm", "systems_seen", "code",
                        "model_description", "auto_cause", "adjudication"],
            "rows": rows}


def replay_coding(artifact: Path) -> dict:
    """Recompute every per-arm rate from the stored per-note tallies and refuse
    to agree with a stored aggregate it cannot reproduce. Hard-errors on a
    vocab_version mismatch, because the vocabulary is correctly absent from the
    response cache key but does change the verified rate (spec §6)."""
    payload = json.loads(Path(artifact).read_text(encoding="utf-8"))

    stored_vocab = payload.get("vocab_version")
    if stored_vocab != vocab.VOCAB_VERSION:
        raise ValueError(
            f"Replay refuses: stored vocab_version {stored_vocab!r} differs from "
            f"current {vocab.VOCAB_VERSION!r}. A warm-cache re-run under a bumped "
            f"pin recomputes a different rate from byte-identical responses.")

    out: dict[str, dict] = {}
    for arm, block in payload["arms"].items():
        tallies = [NoteTally(**{**t, "latency_ms": t.get("latency_ms")})
                   for t in block["notes"].values()]
        recomputed = aggregate_tallies(tallies)
        for name in ("verified_rate", "not_found_rate",
                     "pessimistic_verified_rate", "unchecked_share",
                     "floor_lower", "floor_upper"):
            stored = block.get(name)
            got = recomputed[name]
            if stored is None and got is None:
                continue
            if stored is None or got is None or abs(stored - got) > 1e-9:
                raise ValueError(
                    f"Replay does not match for arm {arm} {name}: recomputed "
                    f"{got}, artifact stores {stored}. Edited artifact or a "
                    f"metric change.")
        # Cost/latency are measured-once: verify stored aggregates equal the
        # recomputation from the stored per-note values, not from responses.
        for name in ("input_tokens", "output_tokens",
                     "latency_notes_contributing"):
            if block.get(name) != recomputed[name]:
                raise ValueError(
                    f"Replay: stored {name} for arm {arm} does not match its "
                    f"per-note values.")
        out[arm] = recomputed
    return out
```

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_coding_benchmark.py -v`

- [ ] **Step 5: Commit.**

```bash
ruff check governance/coding_benchmark.py
git add governance/coding_benchmark.py tests/test_coding_benchmark.py
git commit -m "feat(P2-4): code-free committed artifact, roster, and replay with vocab guard"
```

### Task 7.2: Pilot diagnostics (pure)

The pilot answers, before the full spend, whether the Equivalent branch is even reachable at n=120, and reports every guard statistic (spec §2, §4, §8). These functions are pure over already-computed per-note pilot results; the actual pilot run is a human-gated step in Chunk 8. Reachability: Equivalence needs `SE_diff < DELTA/1.96`; `SE_diff = SE_arm * sqrt(2(1 - rho))`, so it is attainable only above a threshold `rho` at the achieved n.

- [ ] **Step 1: Write failing tests** (append to `tests/test_coding_pilot.py`)

```python
import pytest
from governance.coding_pilot import (
    pearson_rho, equivalence_attainable, pilot_v_check,
)


def test_pearson_rho_of_identical_series_is_one():
    assert pearson_rho([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_pearson_rho_is_none_on_degenerate_input():
    assert pearson_rho([1.0], [1.0]) is None            # <2 points
    assert pearson_rho([2.0, 2.0], [1.0, 3.0]) is None  # zero variance one side


def test_equivalence_attainable_uses_the_se_diff_threshold():
    # DELTA/1.96 ~ 0.765 points. High rho shrinks SE_diff below it -> attainable.
    assert equivalence_attainable(se_arm_points=0.5, rho=0.9, n_pilot=5,
                                  n_target=120) is True
    # Low rho and a big SE keep SE_diff above the threshold -> not attainable.
    assert equivalence_attainable(se_arm_points=5.0, rho=0.0, n_pilot=5,
                                  n_target=120) is False


def test_pilot_v_check_flags_a_high_absolute_v():
    # v >= 98 in absolute terms escalates even inside the 5-point band (spec §4).
    assert pilot_v_check(v_pilot=98.5)["escalate"] is True
    assert pilot_v_check(v_pilot=94.0)["escalate"] is False
    # a >5 point deviation in either direction escalates
    assert pilot_v_check(v_pilot=88.0)["escalate"] is True
```

- [ ] **Step 2: Run, expect FAIL.** `pytest tests/test_coding_pilot.py -k "rho or attainable or v_check" -v`

- [ ] **Step 3: Implement** (append to `governance/coding_pilot.py`):

```python
import math

from governance.coding_decision import DELTA


def pearson_rho(xs: list[float], ys: list[float]) -> float | None:
    """Between-arm correlation of per-note not-found rates. None when <2 points
    or either side has zero variance (rho is undefined, not 0)."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def equivalence_attainable(*, se_arm_points: float, rho: float | None,
                           n_pilot: int, n_target: int) -> bool:
    """Is the Equivalent branch reachable at n_target? Extrapolate the pilot's
    per-arm SE by the usual sqrt(n) scaling, form SE_diff = SE_arm*sqrt(2(1-rho))
    at the target n, and compare to DELTA/1.96 (spec §2). A None rho is treated
    as 0 (the pessimistic, wider-interval assumption)."""
    r = 0.0 if rho is None else rho
    se_arm_target = se_arm_points * math.sqrt(n_pilot / n_target)
    se_diff = se_arm_target * math.sqrt(2.0 * (1.0 - r))
    return se_diff < DELTA / 1.96


def pilot_v_check(v_pilot: float, projection: float = 94.0) -> dict:
    """Surface delta-sizing concerns to a human (spec §4). Never changes a
    threshold; delta stays the pre-registered 1.5."""
    escalate = abs(v_pilot - projection) > 5.0 or v_pilot >= 98.0
    return {"v_pilot": v_pilot, "projection": projection, "escalate": escalate}
```

- [ ] **Step 4: Run, expect PASS.** `pytest tests/test_coding_pilot.py -v`

- [ ] **Step 5: Commit.**

```bash
ruff check governance/coding_pilot.py
git add governance/coding_pilot.py tests/test_coding_pilot.py
git commit -m "feat(P2-4): pilot diagnostics (rho, equivalence reachability, v check)"
```

### Task 7.3: The CLI and Makefile

Model on `scripts/run_structuring_eval.py`: verify the split before any spend, support `--pilot`, the full run, and `--replay`. This wires the pieces; the money-spending invocations are documented as human-gated in Chunk 8. Keep the CLI thin — orchestration logic stays in the tested modules.

- [ ] **Step 1: Write `scripts/run_coding_benchmark.py`.** Structure (no new test file; it is a thin driver over tested modules, mirroring how `run_structuring_eval.py` is untested glue):

```python
"""P2-4 coding routing benchmark CLI.

    python scripts/run_coding_benchmark.py --pilot
    python scripts/run_coding_benchmark.py            # full run (human-gated; spends)
    python scripts/run_coding_benchmark.py --replay governance/eval_artifacts/<f>.json

Verifies the held-out split before any API call. Refuses to name a cost winner
if governance/pricing.json is absent, leaving ROUTING["coding"] unchanged.
Never writes to agent_decisions (the benchmark does not call agent.run()).
"""
```

The CLI must:
1. `--replay PATH`: call `replay_coding(PATH)`, print the recomputed per-arm rates, return 0. No split verification needed (offline).
2. Otherwise call `verify_split()` first; on `SplitDriftError` print and return 1.
3. Define the two arms explicitly: `ARM_A = ("claude-sonnet-5", "xhigh")`, `ARM_B = ("claude-opus-4-8", "high")`.
4. `--pilot`: load `pin_pilot_ids(5, seed=PINNED_SEED)` from the train loader, build SoapNotes, run both arms on each (cached), compute per-arm tallies, `pearson_rho`, per-arm bootstrap SE, `equivalence_attainable`, guard statistics, `pilot_v_check`, truncation rate, codes-per-note. Print a diagnostic block. **Do not** run the full set. **Do not** report a held-out verified rate. Return 0.
5. Full run: load `load_aci_heldout()`, build SoapNotes, run both arms on each (cached), build per-note `NoteTally`s, `build_analysis_set`, and **if void, stop and report** (do not compute a comparison). Otherwise build `NotePair`s over the intersection, run `paired_bootstrap_bca(seed=PINNED_SEED)`, compute per-arm `ArmGuardStats`, call `decide(...)`, load the price table (`load_price_table()`), apply cost routing only when the branch routes on cost and a table exists, build the committed artifact + roster, write both, and write two `eval_runs` rows via `record_coding_run` (unless `--no-db`). Print the decision and the artifact path. Do **not** modify `shared/llm.py` here — the routing write is the separate human-gated Chunk 8 step.
6. Flags: `--no-db`, `--replay`, `--pilot`, `--window-label v1`, `--workers` (default small, e.g. 4; xhigh latency is 10–24 s/note per the P2-3 live run, so fan-out helps).
7. Pin `PILOT_SEED` and `BOOTSTRAP_SEED` as module constants and print them, so both draws are recorded and reproducible (spec §4, §8).

Reference `scripts/run_structuring_eval.py:110-204` for the argparse shape, the `verify_split()` guard, the progress callback, and the artifact-path print.

- [ ] **Step 2: Smoke-test the CLI wiring offline** (no spend): run `--replay` against a committed artifact once one exists, and confirm `--help` works.

Run: `python scripts/run_coding_benchmark.py --help`
Expected: usage text, exit 0.

- [ ] **Step 3: Add Makefile targets** (after the `eval-structuring-replay` target):

```makefile
# The newest coding-benchmark committed artifact (never the .full.json roster).
CODING_ARTIFACT ?= $(shell ls -t governance/eval_artifacts/coding_*.json 2>/dev/null | grep -v '\.full\.json' | head -1)

# P2-4 coding routing benchmark. The pilot is cheap; the full run spends real
# money (240 calls at xhigh and high) and is human-gated. See the runbook in
# docs/superpowers/plans/2026-07-22-p2-4-coding-routing-benchmark.md.
coding-benchmark-pilot:
	python scripts/run_coding_benchmark.py --pilot

coding-benchmark:
	python scripts/run_coding_benchmark.py

coding-benchmark-replay:
	python scripts/run_coding_benchmark.py --replay $(CODING_ARTIFACT)
```

Add `coding-benchmark coding-benchmark-pilot coding-benchmark-replay` to the `.PHONY` line and the `help` echo.

- [ ] **Step 4: Verify the roster stays uncommitted.** The roster is written as `governance/eval_artifacts/coding_<stamp>.full.json`, which the existing `governance/eval_artifacts/*.full.json` gitignore rule already excludes. Confirm:

Run: `git check-ignore governance/eval_artifacts/coding_test.full.json`
Expected: the path echoes back (it is ignored). If it does not, add `governance/eval_artifacts/coding_*.full.json` to `.gitignore`.

- [ ] **Step 5: Lint and commit.**

```bash
ruff check scripts/run_coding_benchmark.py
git add scripts/run_coding_benchmark.py Makefile
git commit -m "feat(P2-4): coding-benchmark CLI (pilot, full, replay) and Make targets"
```

### Task 7.4: Full suite green

- [ ] **Step 1: Run the whole suite and lint.**

Run: `make test && make lint`
Expected: all pass (the pre-existing 201-passed/1-xfailed baseline plus the new P2-4 tests), ruff clean.

- [ ] **Step 2: If anything fails, fix before proceeding.** Do not advance to the money-spending chunk on a red suite.

- [ ] **Step 3: Commit any fixes.**

```bash
git add -A
git commit -m "test(P2-4): full suite green before the human-gated run"
```

---

## Chunk 8: FY2025 floor measurement, and the human-gated execution runbook

Everything above builds and unit-tests the machinery and spends nothing. This chunk is the measurement that decides the cause-2 floor pin, and the ordered, human-gated sequence that actually spends money and, at the very end, changes production routing. **Do not run any money-spending or routing-writing step as part of ordinary execution.** Each one is marked **STOP** and requires explicit user confirmation.

**Files:**
- Create: `scripts/measure_fy2025_diff.py`
- Modify (conditionally): `data/vocab/PROVENANCE.md`, `governance/coding_benchmark.py` (floor-pin wiring)
- Modify (terminal, human-gated): `shared/llm.py`, `docs/MODEL-EFFORT-GUIDE.md`, `docs/ROADMAP.md`

### Task 8.1: Measure `|FY2025 \ FY2026|` (spec §5a)

Cause 2 (real code absent from the current pin) is only worth vendoring if FY2025 contains codes **deleted from** FY2026. ICD-10-CM churn is overwhelmingly additive, so this set is expected to be tiny; it must be measured, not assumed. An old code that still exists in FY2026 verifies fine and contributes nothing to the floor.

- [ ] **Step 1: Write `scripts/measure_fy2025_diff.py`.** It takes the path to an already-downloaded, extracted FY2025 codes member and diffs its normalized set against the vendored FY2026 set:

```python
"""Measure |FY2025 \\ FY2026| for the P2-4 cause-2 floor decision (spec §5a).

The only codes a prior pin can rescue are those in FY2025 AND deleted from
FY2026. Usage:

    python scripts/measure_fy2025_diff.py --fy2025 /path/to/icd10cm_codes_2025.txt

Download and extraction are a human step: the CMS HTML pages 403 automated
fetchers while the direct file URLs return 200 (see data/vocab/PROVENANCE.md).
FY2025 has an original AND a mid-year update; record which one. Do NOT parse the
icd10cm_order_*.txt sibling: its lines lead with a sequence number and a
first-token parse loads sequence numbers as codes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from shared import vocab   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fy2025", type=Path, required=True,
                    help="extracted FY2025 icd10cm_codes_*.txt (NOT the order file)")
    args = ap.parse_args()

    fy2025 = {vocab.normalize(line.split(None, 1)[0])
              for line in args.fy2025.read_text(encoding="utf-8").splitlines()
              if line.strip()}
    fy2026 = vocab.load_icd10()

    only_2025 = fy2025 - fy2026
    print(f"FY2025 codes:            {len(fy2025)}")
    print(f"FY2026 codes (vendored): {len(fy2026)}")
    print(f"|FY2025 \\ FY2026|:       {len(only_2025)}   "
          f"(codes a prior pin could rescue)")
    print(f"|FY2026 \\ FY2025|:       {len(fy2026 - fy2025)}   "
          f"(additive churn, irrelevant to the floor)")
    if only_2025:
        print("Sample deleted codes:", sorted(only_2025)[:20])
    print()
    print("Decision rule (spec §5a): if |FY2025 \\ FY2026| is in the tens against "
          "~18 not-found events per arm, it cannot move the attribution. Drop the "
          "vendoring and record vocab_floor_version = 'none'. Otherwise vendor "
          "FY2025 with a dated release, the order-file trap avoided, and the "
          "member path recorded, and pass its normalized delta as floor_members "
          "to coding_metrics.floor_band. VOCAB_VERSION does NOT change (spec §5a).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Commit the measurement script** (it does not need the download to be committed).

```bash
ruff check scripts/measure_fy2025_diff.py
git add scripts/measure_fy2025_diff.py
git commit -m "feat(P2-4): FY2025-vs-FY2026 diff measurement for the cause-2 floor pin"
```

- [ ] **Step 3: STOP — human downloads FY2025 and runs the diff.** Suggest the user run, in-session, something like `! python scripts/measure_fy2025_diff.py --fy2025 <path>` after downloading the FY2025 code-descriptions zip (the direct file URL, not the HTML page) and extracting the `icd10cm_codes_2025.txt` member. Record the printed `|FY2025 \ FY2026|`.

- [ ] **Step 4: Decide and record.**
  - If the diff is tens-scale (expected): `vocab_floor_version = "none"`, `floor_members = None`. Cause 2 stays in the residual. Nothing else changes.
  - If it is large enough to matter: with the user, vendor FY2025 as a floor pin (dated release, order-file trap avoided, member path recorded in `data/vocab/PROVENANCE.md`), load its normalized `FY2025 \ FY2026` delta as `floor_members`, and thread it through `build_committed_artifact` so `floor_band` attributes cause 2. **`VOCAB_VERSION` stays unchanged** (spec §5a); the floor pin lives only in `vocab_floor_version`. Add a test that a known deleted code is attributed cause 2, not cause 1.

### Task 8.2: The price table (spec §8)

- [ ] **Step 1: STOP — request `governance/pricing.json` from the user.** It carries per-model input/output prices, a source URL, and a retrieval date, for `claude-sonnet-5` and `claude-opus-4-8`. Do not invent prices. Shape:

```json
{
  "source": "https://www.anthropic.com/pricing (or the console pricing page)",
  "retrieved": "2026-07-22",
  "prices": {
    "claude-sonnet-5": {"input_per_mtok": 0.0, "output_per_mtok": 0.0},
    "claude-opus-4-8": {"input_per_mtok": 0.0, "output_per_mtok": 0.0}
  }
}
```

- [ ] **Step 2: If provided, commit it; if not, proceed knowing the cost branch is terminal.** A missing table means the Equivalent/Inconclusive branches leave `ROUTING["coding"]` unchanged and name no winner (spec §2). This is a stated outcome, not a failure.

```bash
git add governance/pricing.json   # only if the user supplied it
git commit -m "chore(P2-4): add user-supplied model price table"
```

### Task 8.3: Run the pilot (spec §8) — STOP, human-gated

- [ ] **Step 1: STOP — confirm with the user before spending.** The pilot is 5 train notes × 2 arms = 10 calls at xhigh and high. Confirm the user wants to proceed and that `ANTHROPIC_API_KEY` is set in `.env`.

- [ ] **Step 2: Run `make coding-benchmark-pilot`.** It prints the pinned pilot ids and seed, then per-arm token usage, latency, truncation rate, codes-per-note, its own `v`, every section-2 guard statistic, `rho`, the design effect, and whether the Equivalent branch is attainable at n=120. It does **not** report a held-out verified rate.

- [ ] **Step 3: STOP — surface the pilot to the user before the full run.** Report `rho`, `equivalence_attainable`, the guard statistics (are the unchecked and floor guards plausibly firing?), and `pilot_v_check` (is `v` within 5 points of 0.94 and below 0.98?). If `equivalence_attainable` is False, record that the full run can return only Difference or Inconclusive, and confirm the user still wants to spend. **Do not redraw the pilot** — the draw is pinned so it cannot be shopped for a nicer answer (spec §8).

### Task 8.4: Run the full benchmark (spec §4, §6) — STOP, human-gated

- [ ] **Step 1: STOP — confirm the full spend.** 120 notes × 2 arms = 240 calls at xhigh and high, 10–24 s/note at xhigh (P2-3 live). Confirm with the user.

- [ ] **Step 2: Run `make coding-benchmark`.** Watch for: any `observed model does not match requested` error (hard-fails the run by design — spec §6), the void message if the intersection falls below 108, and the printed decision (branch, guards tripped, and the winning configuration if one is named).

- [ ] **Step 3: Verify the artifact replays.** Run `make coding-benchmark-replay`. It must recompute every per-arm rate from the committed tallies and agree. This is the acceptance evidence that the number is a fact, not a claim (spec §6).

- [ ] **Step 4: Confirm the eval_runs rows.** Two rows (one per arm), each with `accuracy`/`f1`/`precision`/`recall` NULL, `model_effort` set, and the comparison block duplicated into both `metrics` blobs. `n_examples` equals the intersection size for both.

### Task 8.5: Apply the routing decision (terminal) — STOP, human-gated

This is the one step that changes production behavior. It runs only after Tasks 8.3–8.4 and with explicit user confirmation.

- [ ] **Step 1: Determine the action from the decision.**
  - **Difference** → route to the lower-not-found arm (no price table needed). Update `ROUTING["coding"]` in `shared/llm.py` to the winning `(model, effort)` with a one-line note recording the artifact and the branch.
  - **Equivalent or Inconclusive, price table present** → route on cost to the cheaper arm; update `ROUTING["coding"]` with a one-line cost note.
  - **Equivalent or Inconclusive, price table absent** → **leave `ROUTING["coding"]` unchanged.** Record in the artifact/commit message that routing is deferred for want of a price table, and name no winner (spec §2).
  - **Void** → leave routing unchanged; the run produced no result to route on.
  - If the standard and pessimistic framings disagree on the better arm, report that disagreement as the finding regardless of branch (spec §2).

- [ ] **Step 2: If a winner is named, update `shared/llm.py` and `docs/MODEL-EFFORT-GUIDE.md` Layer B.** Record the winning **configuration**, not "the winning model." Example note in `shared/llm.py` above `ROUTING`:

```python
# coding: P2-4 benchmark (artifact governance/eval_artifacts/coding_<stamp>.json,
# branch=<branch>) routed to <model> at <effort>. This is a (model, effort)
# CONFIGURATION result, not a model result, and not a coding-accuracy result.
```

- [ ] **Step 3: STOP — commit the routing change only with user confirmation.**

```bash
git add shared/llm.py docs/MODEL-EFFORT-GUIDE.md governance/eval_artifacts/coding_*.json
git commit -m "feat(P2-4): route coding to the winning configuration per the benchmark"
```

### Task 8.6: Close the P2-4 gate (spec §9, CLAUDE.md phase gates)

- [ ] **Step 1: State the gate and show evidence.** P2-4's exit per `docs/MODEL-EFFORT-GUIDE.md` is "an eval that decides routing, must be sound." Evidence: the two `eval_runs` rows, the committed artifact, a green `make coding-benchmark-replay`, and the decision with its guards. Present these to the user.

- [ ] **Step 2: Update `docs/ROADMAP.md`** with the measured comparison-block numbers (delta point, CI, branch, guards), the floor pin used (`vocab_floor_version`), and the routing outcome.

- [ ] **Step 3: Update the memory** `care-ops-phase-status.md`: P2-4 complete, the branch/commit, the decision branch and whether routing changed, and whether the price table was present. Note explicitly that the number is a configuration verified-rate comparison, never coding accuracy (reinforces [[coding-metric-has-no-gold-set]]).

- [ ] **Step 4: STOP — get explicit user confirmation before starting P2-5.** Do not advance phases without it (CLAUDE.md hard stop). After this max-effort task, remind the user that `max` is session-only and to step back down (e.g. `/effort high`) for routine work.

---

## Acceptance criteria (from spec §9)

Map each to the task that satisfies it before calling P2-4 done:

- [ ] `call_detailed()` returns `LLMResult`; `call()` unchanged for existing callers; `_UNSET` distinguishes not-overridden from explicit-None — **Task 1.1, 1.2**
- [ ] Test asserts the outbound request carries the model and effort overrides — **Task 1.2**
- [ ] `parse_and_enrich` extracted, shared by `run()` and the benchmark — **Task 1.3**
- [ ] `observed_model` recorded per note; family mismatch fails the run — **Task 6.4**
- [ ] `ALTER TABLE` for `metrics` and `model_effort`, idempotent — **Task 5.2**
- [ ] `record_coding_run` added; `record_structuring_run` untouched — **Task 5.3**
- [ ] Phase 3 consumers verified against an all-NULL accuracy family (none exist yet; contract pinned) — **Task 5.3**
- [ ] `prompt_version` folds in effort, prompt hash, `max_tokens` — **Task 6.4** (`_cache_key`)
- [ ] Dedup on `normalize(code)` alone, per note; `verified_count`/`not_found_count` unused; doubled code counts once; not_found/unchecked conflict resolves to not_found — **Task 2.1**
- [ ] All rates/thresholds/endpoints in points; proportion-scale CI cannot satisfy Equivalent — **Task 4.1**, **Task 2.x**
- [ ] Bootstrap uses shared indices, resamples `n = |intersection|`; identical arms give every replicate delta exactly 0 — **Task 3.1, 6.5**
- [ ] BCa hand-rolled with pinned tie convention and jackknife acceleration; degenerate acceleration recorded not zeroed; `None` denominators handled; seed and replicate count recorded — **Task 3.1**
- [ ] Cache stores a serialized `LLMResult`; `observed_model` and token counts survive a hit — **Task 6.1, 6.4**
- [ ] Missing price table is a terminal state leaving `ROUTING` unchanged — **Task 5.1, 8.5**
- [ ] `|FY2025 \ FY2026|` measured and recorded; FY2025 vendored only if it clears; `VOCAB_VERSION` unchanged — **Task 8.1**
- [ ] `_PLACEHOLDERS` catches `NONE`, `UNKNOWN`, `TBD` — **Task 2.1**
- [ ] Floor per arm as bounds; cause 3 an upper bound — **Task 2.1**
- [ ] Roster written to a gitignored `.full.json`, not committed — **Task 7.1, 7.3**
- [ ] Stratification on `plan == ""`, reported as 27 and 93 — **Task 6.2**
- [ ] Rank-invariance check reported descriptively, no decision consequence — see note below
- [ ] `governance/pricing.json` present with source/date, or run declines a cost winner — **Task 8.2, 5.1**
- [ ] Pilot from train split reports `rho`, design effect, equivalence attainable, before the full run — **Task 6.3, 7.2, 8.3**
- [ ] Analysis set is the intersection; run voids below 90% — **Task 6.5**
- [ ] Artifact carries the top-level comparison block including `delta_ci95` and `branch_fired` — **Task 7.1**
- [ ] Replay recomputes every rate and hard-errors on `vocab_version` mismatch; cost/latency verified against stored per-note values — **Task 7.1**
- [ ] Decision rule applied literally, including `abs(d)` and Inconclusive; winning configuration recorded when named, `ROUTING` unchanged when the price table is absent — **Task 4.1, 8.5**
- [ ] No artifact describes a number as coding accuracy or attributes a result to a model — enforced throughout; verify in the artifact review at **Task 8.4**

### The rank-invariance check (spec §3), deferred as optional

The 15-note descriptive P1-2-path check is context, not a gate: at ~2 not-found events per arm a sign computed on it is near a coin flip, so an earlier draft's hard veto would have vetoed at random. It is **not** required for the routing decision and is **not** on the critical path. If the user wants it, it is a small add-on after Task 8.4: run 15 held-out notes through the real `structure_note` path (not `build_soap_from_reference`), then both arms, and report the per-arm not-found rates descriptively with an explicit "no decision consequence" label. Left out of the numbered tasks deliberately to keep the money-spending surface minimal.

---

## Notes for the executor

- **Mock targets.** `agent.py` does `from shared.llm import ... call`, binding the name at import time; `governance/coding_benchmark.py` does `from shared.llm import call_detailed`, so tests patch `governance.coding_benchmark.call_detailed`, not `shared.llm.call_detailed`. Same trap as `tests/test_coding_agent.py`'s header documents.
- **No em-dash rule** (CLAUDE.md) applies to any generated text, including artifact notes and commit messages.
- **Windows/OneDrive.** Recursive `grep`/`find` over the repo is slow; use the Grep/Glob tools, not shell recursion. `psql` is not local; the DB write path is exercised only when a container DB is up, so keep DB writes behind `--no-db` for dry runs.
- **Effort.** This plan was written at Opus 4.8 / max. Executing subagents should run the metric-critical chunks (2, 3, 4, 7 replay) at high or above; the wiring chunks tolerate Sonnet at medium.
- **Never** let a subagent "fix" a failing identical-arms bootstrap test by loosening `== 0.0` to `approx`. That test failing means the arms were resampled independently, which is a real bug in the resampling, not a floating-point tolerance issue.
- **The two aggregation paths are a deliberate double-entry check, not redundancy.** The orchestration (Task 7.3 full run) builds one `NoteTally` per note per arm and drives BOTH the guard statistics (`ArmGuardStats`) and the committed artifact from `aggregate_tallies`, so there is one serialized source of truth the replay recomputes from. `ArmSummary`/`aggregate_arm` (Task 2.2) is an **independent** implementation of the same pooled-rate math over `DedupedCode`, kept as a test oracle: add a cross-check test that, for a shared input, `aggregate_arm` and `aggregate_tallies` agree on `verified_rate`, `not_found_rate`, and `unchecked_share` to 1e-9. Two independent implementations agreeing is a strong correctness signal on headline-adjacent math; if the executor would rather collapse to one path, that is acceptable only if `note_agreement` (which needs the deduped keys, absent from `NoteTally`) is preserved and computed per note at run time.
- **`_cache_key` and `_cache_version_string` must produce the identical version string** `f"{effort}|{hash_prompt(CODING_SYSTEM)}|max{CODING_MAX_TOKENS}"`. Have `_cache_key` call `_cache_version_string(effort)` so the cache key and the artifact's recorded `prompt_version` can never drift. Delete the illustrative unused `_CODING_VERSION` module line.
- **`agreement` is descriptive only.** It is stored in the artifact and printed, but it is never passed to `decide()` and never influences routing (spec §1). A reviewer should confirm no code path threads `agreement` into the decision.
- **Plan-review option.** This plan was self-reviewed against the spec's §9 acceptance list, not passed through the writing-plans plan-document-reviewer subagent (that would spawn an agent, which is off by default here). If you want that second pass before execution, say so and it can be dispatched.
