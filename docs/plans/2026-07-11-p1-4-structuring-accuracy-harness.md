# P1-4 Note-Structuring Accuracy Harness: Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. TDD throughout: write the failing test, watch it fail, implement, watch it pass, commit.

**Goal:** Score the SOAP structuring pipeline against the frozen held-out set, print a measured accuracy, and write an `eval_runs` row, such that the number is reproducible offline and defensible under scrutiny.

**Architecture:** Reference clinician notes are parsed into SOAP buckets by a committed header mapping. Each reference section is decomposed into atomic clinical facts, and each fact inherits its SOAP label from the *human* section header, never from a model. A pinned judge then answers two questions per note: was each reference fact captured, and in which section (recall and placement); and is each generated fact supported by the transcript (precision, which is the hallucination check). Every LLM call is content-addressed and cached, and every run emits a committed artifact from which the headline number recomputes offline with zero API calls.

**Tech Stack:** Python 3.10, pydantic, psycopg, scikit-learn (already present), Anthropic SDK 0.116.0, faster-whisper, pytest, ruff.

---

## Why the metric is shaped this way

The output is four free-text SOAP sections, so exact-field-match is meaningless. The metric is fact-level F1, with a deliberate asymmetry that must be stated out loud because it is the first thing a sharp reviewer will probe:

- **Recall is scored against the clinician note.** Of the atomic clinical facts the clinician wrote, how many did the model capture *and file in an acceptable SOAP section*? The clinician note is the gold standard for **what matters**.
- **Precision is scored against the transcript, not the note.** Of the atomic facts the model wrote, how many are supported by the transcript? The clinician note is a *selective summary*, so a generated fact that appears in the transcript but not in the note is a legitimate inclusion choice, not an error. A generated fact supported by **neither** is a hallucination, which is exactly what the P1-2 structuring prompt forbids ("Never invent, infer, or assume"). The transcript is the gold standard for **what is true**.
- **F1** is the harmonic mean of those two. This is the headline.
- **Section-placement accuracy** (stored in `eval_runs.accuracy`) is: of the reference facts the model captured at all, what fraction landed in the right SOAP section? This isolates the *structuring* skill from the *capture* skill.
- **Hallucination rate** is `1 - precision`, reported alongside.

**A/P fusion.** 51 of the 120 held-out ACI-Bench notes fuse assessment and plan into a single `ASSESSMENT AND PLAN` section, so there is no ground truth separating A from P in those notes. Every fact therefore carries an `acceptable` set of SOAP buckets: `{assessment, plan}` if it came from a fused section, otherwise the single exact bucket. All 120 notes are scored, the fused count is disclosed in the artifact and the printed report, and strict placement accuracy on the 69 separable notes is reported separately so the leniency is visible rather than hidden.

**Where the headline number comes from.** ACI-Bench held-out (n=120) is the only held-out data with clinician-sectioned reference notes, so it carries the headline. PriMock57 held-out (n=7) supplies the Phase 1 exit gate's end-to-end audio-in run plus a human-authored `highlights` recall number, written as its own `eval_runs` row with `accuracy` NULL because its GP notes are not SOAP-sectioned and placement cannot be honestly scored. Both facts are disclosed.

## Two bugs this plan is explicitly built to prevent

1. **CRLF.** The ACI challenge CSVs use `\r\n` inside the quoted note field. A `$`-anchored header regex matches nothing, every note parses to zero sections, and the harness cheerfully reports a number computed on an empty reference set. Confirmed live during design. `normalize()` runs first, and a test pins it.
2. **Silently dropped sections and verdicts.** An unmapped section header, or a judge returning fewer verdicts than facts, removes reference facts from the denominator and **inflates recall**. Both raise. Nothing is ever skipped quietly.

---

## File structure

**Create**
- `governance/heldout.py` - loads the held-out set; recomputes the split digest and refuses to run on drift.
- `governance/aci_sections.py` - CRLF-safe section parsing; committed header-to-SOAP mapping.
- `governance/llm_cache.py` - content-addressed disk cache for LLM calls.
- `governance/facts.py` - atomic-fact decomposition (LLM, cached).
- `governance/judge.py` - presence/placement and transcript-support judging (LLM, cached).
- `governance/structuring_eval.py` - orchestration, counts, artifact emission, replay.
- `scripts/run_structuring_eval.py` - CLI entrypoint.
- `tests/test_aci_sections.py`, `tests/test_heldout_guard.py`, `tests/test_structuring_metrics.py`, `tests/test_llm_cache.py`, `tests/test_structuring_eval.py`

**Modify**
- `governance/evaluate.py` - add `score_structuring()` and `record_structuring_run()`. Leave `score()`/`record_run()` untouched so `tests/test_evaluate.py` keeps passing.
- `shared/llm.py` - add the `eval_judge` route and a `temperature` parameter. Model routing stays in one place, per CLAUDE.md.
- `services/intake/transcribe.py` - add dual-track (doctor + patient wav) transcription for PriMock57.
- `Makefile`, `.gitignore`, `docs/HELD-OUT-POLICY.md`, `README.md`.

---

## Chunk 1: Deterministic foundations

No API calls. Everything here runs in CI for free.

### Task 1: Leak guard

**Files:** Create `governance/heldout.py`, `tests/test_heldout_guard.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_heldout_guard.py
import json
import pytest
from governance.heldout import SplitDriftError, verify_split


def test_verify_split_passes_on_the_committed_lock():
    records = verify_split()
    assert any(r.split == "heldout" for r in records)


def test_verify_split_raises_when_the_lock_digest_does_not_match(tmp_path):
    bad = tmp_path / "lock.json"
    bad.write_text(json.dumps({"digest": "0" * 64}), encoding="utf-8")
    with pytest.raises(SplitDriftError, match="drifted"):
        verify_split(lock_path=bad)
```

- [ ] **Step 2: Run it and watch it fail**

`pytest tests/test_heldout_guard.py -v` -> FAIL, `ModuleNotFoundError: governance.heldout`

- [ ] **Step 3: Implement `governance/heldout.py`**

`verify_split()` recomputes `build_all_records(data_root)` and `manifest_digest(...)` from `shared/splits.py`, compares against the `digest` in `scripts/heldout_split.lock.json`, and raises `SplitDriftError` on mismatch. Then add `load_aci_heldout()` and `load_primock_heldout()` returning `HeldoutExample(dataset, encounter_id, transcript, reference_note, highlights)`, filtered to `split == "heldout"` and asserting no train/dev id leaks into the result.

- [ ] **Step 4: Run and watch it pass**
- [ ] **Step 5: Commit** - `feat(P1-4): refuse to score against a drifted held-out split`

### Task 2: CRLF-safe section parsing and the SOAP mapping

**Files:** Create `governance/aci_sections.py`, `tests/test_aci_sections.py`

- [ ] **Step 1: Write the failing tests** - the CRLF regression is the important one.

```python
CRLF_NOTE = "CHIEF COMPLAINT\r\n\r\nCough.\r\n\r\nASSESSMENT AND PLAN\r\n\r\nURI. Rest.\r\n"

def test_crlf_note_parses_identically_to_lf():
    assert parse_sections(CRLF_NOTE) == parse_sections(CRLF_NOTE.replace("\r\n", "\n"))

def test_crlf_note_finds_its_sections():
    assert set(parse_sections(CRLF_NOTE)) == {"CHIEF COMPLAINT", "ASSESSMENT AND PLAN"}

def test_fused_assessment_and_plan_accepts_either_bucket():
    sections = bucket_sections(CRLF_NOTE)
    fused = [s for s in sections if s.header == "ASSESSMENT AND PLAN"][0]
    assert fused.acceptable == frozenset({"assessment", "plan"})

def test_separate_assessment_accepts_only_assessment():
    note = "ASSESSMENT\n\nURI.\n\nPLAN\n\nRest.\n"
    a = [s for s in bucket_sections(note) if s.header == "ASSESSMENT"][0]
    assert a.acceptable == frozenset({"assessment"})

def test_unknown_header_raises_rather_than_dropping_facts():
    with pytest.raises(UnknownSectionError, match="inflate recall"):
        bucket_sections("BILLING NOTES\n\nSomething.\n")

def test_every_header_in_the_heldout_set_is_mapped():
    # guards the mapping against dataset surprises
    for ex in load_aci_heldout():
        bucket_sections(ex.reference_note)
```

- [ ] **Step 2: Run and watch them fail**
- [ ] **Step 3: Implement.** `normalize()` first, then the header regex, then `HEADER_TO_BUCKET` covering all 28 headers enumerated in the held-out set, then `bucket_sections()` returning `list[RefSection(header, body, acceptable: frozenset)]`. Unknown header raises `UnknownSectionError` whose message says dropping it would inflate recall.
- [ ] **Step 4: Run and watch them pass**
- [ ] **Step 5: Commit** - `feat(P1-4): CRLF-safe ACI section parsing with an auditable SOAP mapping`

### Task 3: Content-addressed LLM cache

**Files:** Create `governance/llm_cache.py`, `tests/test_llm_cache.py`

- [ ] **Step 1: Write the failing tests** - roundtrip; a different model is a miss; a different prompt version is a miss.
- [ ] **Step 2: Run and watch them fail**
- [ ] **Step 3: Implement.** Key is `sha256(task | model | prompt_version | payload)`. Values are JSON files under a cache root. Model id and prompt version are *in the key*, so bumping either is a miss rather than a silent reuse of stale judgments.
- [ ] **Step 4: Run and watch them pass**
- [ ] **Step 5: Commit** - `feat(P1-4): content-addressed cache so the metric replays without re-spending`

---

## Chunk 2: Metric math

### Task 4: `score_structuring`

**Files:** Modify `governance/evaluate.py`; create `tests/test_structuring_metrics.py`

- [ ] **Step 1: Write the failing tests**, including a hand-computed vector so the arithmetic is pinned by a human, not by the implementation.

```python
def test_hand_computed_vector():
    c = StructuringCounts(ref_facts=10, captured=8, correctly_placed=6,
                          gen_facts=12, supported=9)
    m = score_structuring(c)
    assert m["recall"] == pytest.approx(0.60)          # 6/10
    assert m["precision"] == pytest.approx(0.75)       # 9/12
    assert m["f1"] == pytest.approx(0.6666667)         # 2*.75*.6/1.35
    assert m["accuracy"] == pytest.approx(0.75)        # 6/8 placement
    assert m["hallucination_rate"] == pytest.approx(0.25)

def test_perfect_and_zero_and_empty_are_all_safe():
    ...  # no ZeroDivisionError on empty inputs
```

- [ ] **Step 2: Run and watch them fail**
- [ ] **Step 3: Implement** `StructuringCounts` and `score_structuring()` in `governance/evaluate.py`, with the docstring spelling out the recall-vs-note / precision-vs-transcript asymmetry. Metric math lives in exactly one place.
- [ ] **Step 4: Run and watch them pass**
- [ ] **Step 5: Commit** - `feat(P1-4): structuring metric math, one place, hand-pinned`

### Task 5: `record_structuring_run`

**Files:** Modify `governance/evaluate.py`

- [ ] **Step 1** Write a test that inserts and reads back one `eval_runs` row (skipped when no database is reachable, so CI without Postgres is unaffected).
- [ ] **Step 2** Watch it fail. **Step 3** Implement the INSERT ... RETURNING id. **Step 4** Watch it pass.
- [ ] **Step 5: Commit** - `feat(P1-4): write structuring metrics to eval_runs`

---

## Chunk 3: The LLM layer

Every call is cached. Every test in this chunk uses a stub, so **CI never needs an API key**.

### Task 6: Judge routing in `shared/llm.py`

**Files:** Modify `shared/llm.py`

- [ ] **Step 1** Read the `claude-api` skill before editing (its trigger rules require it, and this touches model ids and SDK params).
- [ ] **Step 2** Add `"eval_judge": (os.getenv("MODEL_EVAL_JUDGE", "claude-haiku-4-5-20251001"), None)` to `ROUTING`, and add a `temperature: float | None = None` parameter to `call()`. Routing stays in one place per CLAUDE.md. Effort stays `None` for the judge, so `temperature=0` is legal (effort and temperature cannot both be set).
- [ ] **Step 3: Commit** - `feat(P1-4): route the eval judge through shared/llm.py`

### Task 7: Fact decomposition

**Files:** Create `governance/facts.py`

- [ ] **Step 1: Write the failing tests** with a stubbed `call`: n input lines yields n `Fact`s; each fact inherits `acceptable` from its `RefSection` and **never** from the model; a malformed model response raises.
- [ ] **Step 2** Watch fail. **Step 3** Implement `decompose(section: RefSection) -> list[Fact]`, prompt version `v1`, cached, temp 0. Prompt instructs: one atomic clinical fact per line, copy faithfully, do not add, infer, or merge.
- [ ] **Step 4** Watch pass. **Step 5: Commit** - `feat(P1-4): atomic fact decomposition with human-derived section labels`

### Task 8: The judge

**Files:** Create `governance/judge.py`

- [ ] **Step 1: Write the failing tests** with a stubbed `call`. The critical ones:
  - a fact judged present in an `acceptable` section counts as correctly placed;
  - a fact judged present in a non-acceptable section counts as captured but **not** correctly placed;
  - a fused-section fact is correctly placed whether the judge says `assessment` **or** `plan`;
  - **a judge returning fewer verdicts than facts raises** (this is the recall-inflation guard);
  - a judge returning an unknown section name raises.
- [ ] **Step 2** Watch fail. **Step 3** Implement `judge_presence(soap, facts)` and `judge_support(transcript, gen_facts)`, both batched one-call-per-note, both cached, both validating verdict count and ids against the input before returning.
- [ ] **Step 4** Watch pass. **Step 5: Commit** - `feat(P1-4): pinned judge with hard validation against silent fact loss`

---

## Chunk 4: Orchestration, artifact, replay

### Task 9: The harness

**Files:** Create `governance/structuring_eval.py`, `tests/test_structuring_eval.py`

- [ ] **Step 1: Write the failing test** - a full run over 2 fake examples with a stubbed LLM produces the hand-computed metrics and a well-formed artifact.
- [ ] **Step 2** Watch fail.
- [ ] **Step 3: Implement.** For each held-out example: generate the SOAP note (cached), parse the reference into `RefSection`s, decompose to reference facts, decompose the generated note to generated facts, judge presence/placement and transcript support, accumulate `StructuringCounts`. Emit an artifact containing the split digest, both model ids, all prompt versions, the fused-note count, per-fact verdicts, the counts, and the metrics.
- [ ] **Step 4** Watch pass. **Step 5: Commit** - `feat(P1-4): structuring accuracy harness`

### Task 10: Replay - the reproducibility proof

**Files:** Modify `governance/structuring_eval.py`; create `scripts/run_structuring_eval.py`

- [ ] **Step 1: Write the failing test** - `replay(artifact)` recomputes counts and metrics from the stored per-fact verdicts and asserts they equal the stored metrics. Zero API calls.
- [ ] **Step 2** Watch fail. **Step 3** Implement `replay()` plus the CLI: `--dataset aci|primock`, `--limit N`, `--no-db`, `--replay PATH`.
- [ ] **Step 4** Watch pass. **Step 5: Commit** - `feat(P1-4): offline replay recomputes the headline number from the committed artifact`

---

## Chunk 5: Real runs and the gate

### Task 11: The ACI-Bench run (the headline)

- [ ] Run `make eval-structuring` for real on all 120 held-out ACI encounters. Estimated cost roughly 7 to 10 dollars: 120 Sonnet 5 generations at high effort, plus 3 cached Haiku judge calls per note.
- [ ] Commit the artifact to `governance/eval_artifacts/`.
- [ ] Add a CI test that `--replay`s the committed artifact and asserts the headline recomputes. **CI now regression-tests the headline number for free.**
- [ ] Commit - `feat(P1-4): measured structuring accuracy on the ACI-Bench held-out set (n=120)`

### Task 12: PriMock57 end to end (the Phase 1 exit gate)

**Files:** Modify `services/intake/transcribe.py`

- [ ] Add `transcribe_consultation(doctor_wav, patient_wav)`: transcribe both tracks, merge segments by timestamp, render as speaker-labeled dialogue. Test the merge ordering with stubbed segments (no audio, no model download in CI).
- [ ] Run the 7 held-out PriMock57 consultations from **audio** through Whisper, structuring, and highlights-recall scoring. Write the `eval_runs` row with `accuracy` NULL and disclose why.
- [ ] Commit - `feat(P1-4): PriMock57 audio-to-SOAP end to end with highlights recall`

### Task 13: Audit and document

- [ ] **Hand-audit 30 randomly sampled judge verdicts** from the committed artifact and record the agreement rate in `docs/HELD-OUT-POLICY.md`. This is what makes the judge defensible; without it the number rests on an unexamined model.
- [ ] Update `docs/HELD-OUT-POLICY.md`: the harness now machine-verifies the split digest before scoring.
- [ ] Update `README.md`: link the headline metric to `scripts/run_structuring_eval.py`, per the project rule that every claimed number links to the script that produces it.
- [ ] `make lint && make test` clean.
- [ ] Commit - `docs(P1-4): audit the judge and link the metric to its script`

---

## Exit evidence for the P1-4 gate

State the gate, then show all four:
1. Command output from `make eval-structuring` printing the measured accuracy.
2. The `eval_runs` row, queried back from Postgres.
3. `make eval-structuring-replay` reproducing the identical number offline with zero API calls.
4. The hand-audit agreement rate on the judge.

Then stop and get explicit user confirmation before starting P1-5.
