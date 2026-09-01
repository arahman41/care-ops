# P3-1: Evaluation Runner

**Status:** design, approved
**Roadmap task:** P3-1
**Depends on:** P0-5 (held-out split locked, `scripts/heldout_split.lock.json`),
P1-4 (structuring harness and its committed artifacts),
P2-4 (the NULL accuracy-family contract for coding rows)
**Model/effort:** Opus at max per `docs/MODEL-EFFORT-GUIDE.md` line 73
("Metric computation")

---

## 1. The gate, and what it collides with

The roadmap gate:

> Done when `governance/evaluate.py` scores an agent against the held-out set
> for a named window and writes accuracy, F1, precision, and recall to
> `eval_runs`.

Read literally, "an agent" implies any agent. The repository cannot honor that
reading, because computing accuracy, F1, precision and recall requires labels,
and only one thing here has them.

| Agent | Labeled reference set on the held-out split? |
|---|---|
| `note_structuring` | **Yes.** Clinician reference notes, ACI-Bench and PriMock57 |
| `coding` | No. Neither dataset carries gold billing codes (ROADMAP P2-4) |
| `care_gap` | No. The rules are deterministic and unit-tested, which is a correctness property, not a measured accuracy |
| `prior_auth` | No. No held-out encounter carries a labeled prior-auth determination |

This is already the standing position in `docs/ROADMAP.md`:

> Before claiming an accuracy number for any agent, name the labeled set it was
> measured against.

`governance/evaluate.py::score` is `average="binary"`, plainly written for a
Phase 2 or Phase 3 agent, and it has never been fed real data because the labels
it expects do not exist.

**Decision.** P3-1 scores `note_structuring` and refuses, loudly and by name,
for every other agent. The gate is satisfied by the one agent that has a
labeled set, and the refusal is the deliverable that stops the gate being
satisfied dishonestly later.

**Scope note on the file named in the gate.** The gate names
`governance/evaluate.py`. This spec reads the gate as capability-based: the
metric policy, the guard and the writer live in `evaluate.py`, and the
orchestration that drives them lives in a new `governance/eval_runner.py`.
Splitting them keeps `evaluate.py`'s pure arithmetic isolated and separately
testable, which is the property that makes the headline number auditable. This
reading is recorded here deliberately rather than applied quietly.

---

## 2. The invariant this task enforces

> **No agent outside the scoreable registry may ever be written a non-NULL
> `accuracy`, `f1`, `precision` or `recall`.**

This rule exists today only as prose, in three places that nothing checks:

- `docs/ROADMAP.md`, the P2-4 entry and the Phase 2 metric note
- `db/schema.sql`, a comment above the `metrics` and `model_effort` columns
- `governance/evaluate.py::coding_row_params`, a docstring

`coding_row_params` passes four literal `None` values into its tuple. That is a
convention, not a constraint. Nothing prevents a future P3-5 endpoint or P4-1
dashboard from writing a verified rate into `accuracy`, and the resulting number
would be indistinguishable from a real one on a chart.

P3-1 makes it a guard that every writer calls before touching the database.

**What the invariant does not say.** It is not "unscoreable agents get no
rows". Coding legitimately writes `eval_runs` rows with the accuracy family
NULL and its verified rate in the `metrics` JSONB column, and P2-4 established
that contract on purpose. The guard permits exactly that shape and refuses the
inverse.

---

## 3. Window semantics and the time axis

`window_label` denotes **a point in time, with the generation configuration held
fixed**. The held-out set, prompt, model and effort do not vary across windows.
Drift therefore measures change on the vendor's side of a hosted model, which is
the reading that P3-3 and the HTI-1 transparency framing need.

The configuration has not moved since P1-4. Verified 2026-08-27 against the
committed ACI-Bench artifact:

```
current prompt hash : b7b42093e9a7      artifact prompt hash: b7b42093e9a7
current routing     : ('claude-sonnet-5', 'high')
artifact model      : claude-sonnet-5 high
```

So the July run and a run today are comparable, and **P1-4's run becomes window
1 at no cost**.

**The time axis.** `eval_runs.created_at` defaults to `now()`. Backfilling
July's measurement today would stamp it 2026-08-27, and P3-3 ordering by time
would read the trend backwards. `created_at` is therefore **redefined to mean
the time the measurement was taken**, not the time the row was inserted. The
backfill passes the artifact's own `created_at`
(`2026-07-14T03:24:03.340016+00:00` for ACI-Bench); live runs pass `now()`. The
definition is written into `db/schema.sql` as a comment so no later reader
misinterprets the column.

---

## 4. The provenance gap this closes, and the one it cannot

The artifact records the prompt hash, the model and the effort. It does **not**
record `max_tokens`, even though `generate_soap` puts it in the cache key:

```python
version = f"{effort}|{hash_prompt(SYSTEM_PROMPT)}|max{MAX_TOKENS}"
```

and its own docstring explains why that matters:

> max_tokens is in here specifically because it already bit us once: the
> original 1200-token cap silently truncated long encounters.

`MAX_TOKENS` is 8000 today. Whether it was 8000 on 2026-07-14 is **not
recoverable from the artifact**.

`GenerationConfig` records all four fields on every row from now on, which
closes the gap going forward.

**It cannot be closed backwards, and the spec refuses to pretend otherwise.**
The backfilled July row records `max_tokens: null`, meaning "not recorded by the
harness of the day", not `8000`. Writing 8000 there would be inventing evidence
about a run nobody can re-inspect.

The consequence is deliberate and is P3-3's to honor: a comparison whose two
windows differ only because one side's `max_tokens` is `null` is **not
certified comparable**, and P3-3 must report that rather than assume equality.
`differing_fields` returns `max_tokens` as differing when exactly one side is
`null`, so the ambiguity is visible in data rather than resting on this
paragraph.

---

## 5. Components and contracts

### `governance/evaluate.py` (extended; existing arithmetic untouched)

```python
@dataclass(frozen=True)
class ScoreableAgent:
    agent_name: str
    dataset_refs: tuple[str, ...]
    labels_are: str          # what the labels ARE, in words

SCOREABLE: dict[str, ScoreableAgent]     # note_structuring only
UNSCOREABLE: dict[str, str]              # agent_name -> one-sentence reason

class EvalPolicyError(RuntimeError): ...
class UnscoreableAgentError(EvalPolicyError): ...
class UnknownAgentError(EvalPolicyError): ...

def assert_accuracy_family_allowed(
        agent_name: str, metrics: Mapping[str, float | None]) -> None: ...

def record_eval_run(*, agent_name: str, model: str, model_effort: str | None,
                    window_label: str, dataset_ref: str, n_examples: int,
                    metrics: Mapping[str, float | None],
                    provenance: Mapping[str, object],
                    measured_at: datetime) -> int: ...
```

The registry and the guard live here rather than in the runner because they are
policy about what a metric is allowed to mean, which is this file's stated
domain ("metric arithmetic lives *only* here so it cannot drift between
callers"). Keeping them here also means `eval_runner` imports `evaluate` and
never the reverse, so there is no import cycle.

`assert_accuracy_family_allowed` raises:

- `UnknownAgentError` if `agent_name` is in neither dict. A typo in an agent
  name must crash distinctly, never resolve to "unscoreable" and read as a
  deliberate policy decision.
- `UnscoreableAgentError`, carrying the registry's reason, if the agent is
  unscoreable and any of `accuracy`, `f1`, `precision`, `recall` is not `None`.

`record_eval_run` calls the guard **before** opening a connection, so a refused
write never reaches the database. It writes `created_at` explicitly from
`measured_at`, and `provenance` into the `metrics` JSONB column alongside the
run's own metrics.

### `governance/eval_runner.py` (new)

```python
@dataclass(frozen=True)
class GenerationConfig:
    model: str
    effort: str
    prompt_hash: str
    max_tokens: int | None       # None means "not recorded by that harness"

    def differing_fields(self, other: "GenerationConfig") -> tuple[str, ...]: ...

def config_from_artifact(payload: Mapping) -> GenerationConfig: ...

def score_artifact(*, agent_name: str, artifact_path: Path,
                   window_label: str) -> int: ...
```

`score_artifact` is the single scoring entry point. It returns the new
`eval_runs.id`.

### `scripts/run_evaluation.py` (new CLI)

```
python scripts/run_evaluation.py \
    --agent note_structuring \
    --artifact governance/eval_artifacts/structuring_aci-bench-heldout-v1_20260714T032403Z.json \
    --window 2026-07-w2
```

`--no-db` prints the scored result without writing, matching the flag
`scripts/run_structuring_eval.py` already has.

### `scripts/run_structuring_eval.py` (refactored)

Its `record_structuring_run` call is replaced by `record_eval_run`, so there is
one writer rather than two and live runs pass through the same guard.
`record_structuring_run` is then unused and is deleted rather than left as a
second, unguarded path into `eval_runs`.

---

## 6. Data flow

Both cases take one path:

1. `score_artifact` loads the artifact and resolves `agent_name` against the
   registry, raising before any work if it is not scoreable.
2. It calls the existing `structuring_eval.replay(artifact)`. That function
   already recomputes the metrics from per-fact verdicts rather than reading the
   stored numbers back, and raises if the two disagree. P3-1 adds no metric
   arithmetic of its own.
3. `config_from_artifact` assembles the `GenerationConfig`, with `max_tokens`
   `None` when the artifact does not carry it (section 4).
4. `measured_at` is read from the artifact's own `created_at`.
5. `record_eval_run` guards, then writes.

Live generation stays in `scripts/run_structuring_eval.py`, which already does
it well, writes the artifact, and now ingests through the same writer. P3-1 does
not duplicate the generation path.

### Which of the artifact's two metric sets is written

Each structuring artifact carries two: `metrics` over all examples, and
`strict_metrics` over only those whose reference note separates Assessment from
Plan. For ACI-Bench that is 69 of 120, because the A/P leniency applies to the
other 51, and `RunResult.strict_metrics` exists to keep that leniency visible
rather than buried.

**`metrics` is what lands in the accuracy family.** It is the headline P1-4
recorded, so windows stay comparable to window 1.

**`strict_metrics` is deliberately not copied into `eval_runs`.** `replay()`
recomputes only the headline, so writing the strict numbers would mean copying
stored values that nothing re-derived, which is precisely the discipline
`replay()` exists to enforce. Recomputing them is scope P3-1 does not need. They
remain in the committed artifact, which is where the leniency is already
documented. If P3-3 or P3-4 later wants to drift on the strict subset, the work
is to extend `replay()` to recompute and cross-check it, not to trust the
artifact's stored copy.

---

## 7. Error handling

| Condition | Behavior |
|---|---|
| Agent absent from both registries | `UnknownAgentError` |
| Unscoreable agent, non-NULL accuracy family | `UnscoreableAgentError` with the registry's reason |
| Artifact metrics disagree with recomputation | `ValueError` from the existing `replay()` |
| Split digest no longer matches the lock file | existing `SplitDriftError` from `governance/heldout.py::verify_split` |
| Artifact missing `created_at` | `ValueError`. A row with no honest measurement time must not be written at all |

**One behavior that needs its own test.** `replay()` deliberately forces
`accuracy` back to `None` for PriMock57, because placement is not scorable
where the reference note has no SOAP sections. Its own comment states the
stakes:

> the number the harness refused to claim reappears through the very tool whose
> job is to prove the harness honest

`record_eval_run` must preserve that `None`. If it ever wrote `1.0`, the exact
failure `replay()` was built to prevent would occur one layer further down.

---

## 8. Testing

### CI-runnable: no dataset, no database

The redacted artifacts are tracked in git (`git ls-files
governance/eval_artifacts/` returns all three; only the `.full.json` files are
untracked), so replay-based tests need neither `needs_data` nor `needs_db`.

`tests/test_eval_policy.py` (new)

- a scoreable agent resolves and its `labels_are` is non-empty
- each of `coding`, `care_gap`, `prior_auth` raises `UnscoreableAgentError`
  when handed a non-NULL accuracy family, and the message contains its reason
- `coding` with an all-NULL family and a populated `metrics` dict is **allowed**
  (the P2-4 contract stays legal)
- an unknown agent name raises `UnknownAgentError`, not `UnscoreableAgentError`
- `coding_row_params` output still passes the guard, so the existing P2-4 path
  cannot regress

`tests/test_eval_runner.py` (new)

- `differing_fields` detects a change in each of the four fields independently
- `differing_fields` reports `max_tokens` as differing when exactly one side is
  `None`, and reports nothing when both are `None`
- `config_from_artifact` on the committed ACI-Bench artifact yields
  `claude-sonnet-5`, `high`, `b7b42093e9a7`, `max_tokens=None`
- replaying the committed ACI-Bench artifact reproduces
  `f1 == 0.8685633622463043` exactly
- the row parameters built for that artifact carry
  `created_at == 2026-07-14T03:24:03.340016+00:00`, not `now()`

### Behind `needs_db`

Following the module-local `pytest.mark.skipif` idiom already used in
`tests/test_registry.py` and `tests/test_coding_pilot.py`.

- round trip: `score_artifact` on the ACI-Bench artifact writes a row that reads
  back with all four metrics populated and `created_at` equal to the July
  timestamp
- round trip: the PriMock57 artifact writes a row whose `accuracy` is NULL while
  `f1`, `precision` and `recall` are populated
- a direct `record_eval_run` for `coding` with a non-NULL `f1` raises and
  **inserts no row**, verified by a count before and after

---

## 9. Files

| File | Change |
|---|---|
| `governance/evaluate.py` | registry, error types, guard, `record_eval_run`; delete `record_structuring_run` |
| `governance/eval_runner.py` | new: `GenerationConfig`, `config_from_artifact`, `score_artifact` |
| `scripts/run_evaluation.py` | new CLI |
| `scripts/run_structuring_eval.py` | write through `record_eval_run` |
| `db/schema.sql` | comment defining `created_at` as measurement time |
| `tests/test_eval_policy.py` | new |
| `tests/test_eval_runner.py` | new |
| `tests/test_evaluate.py` | keep; add the guard regression for `coding_row_params` |
| `docs/ROADMAP.md` | P3-1 evidence entry |

---

## 10. Known gaps, stated rather than discovered later

1. **One window exists after this task, not two.** P3-1 backfills July as
   window 1. P3-2 is what produces window 2, and it costs a live paid run.
   P3-1's gate does not require a trend.
2. **`max_tokens` for the July window is permanently unknown** (section 4).
   The first fully-provenanced window is whichever run P3-2 performs.
3. **The registry is hand-maintained.** Adding an agent means editing
   `SCOREABLE` or `UNSCOREABLE`. Nothing scans the services directory to detect
   an agent registered in neither, so a genuinely new agent gets a
   `UnknownAgentError` at write time rather than a startup warning. Acceptable
   at four agents; worth revisiting if the count grows.
4. **The guard protects `eval_runs` only.** A future consumer that computes a
   number and renders it without ever writing a row is outside its reach. P3-5
   and P4-1 read from `eval_runs`, so they are covered; anything that bypasses
   the table is not.
5. **PriMock57's window 1 is n=7.** Small enough that its `f1` should not be
   quoted as a headline next to ACI-Bench's n=120. P3-1 stores it; deciding
   what may be said about it belongs to whoever writes the transparency report
   in P3-4.
