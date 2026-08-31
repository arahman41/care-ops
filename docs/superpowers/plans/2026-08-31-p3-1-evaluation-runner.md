# P3-1: Evaluation Runner, implementation plan

**Spec:** `docs/superpowers/specs/2026-08-27-p3-1-evaluation-runner-design.md`
**Branch:** `p3-1-evaluation-runner`
**Model/effort:** Opus at max (`docs/MODEL-EFFORT-GUIDE.md`, "Metric computation")

---

## Order of work

Each step leaves the tree green, so a failure is attributable to the step that
caused it.

1. **`governance/evaluate.py`**: the policy layer. `ACCURACY_FAMILY`,
   `ScoreableAgent`, `SCOREABLE`, `UNSCOREABLE`, the three error types,
   `assert_accuracy_family_allowed`, `resolve_scoreable`, `record_eval_run`.
   Delete `record_structuring_run`. Route `record_coding_run` through the guard.
2. **`governance/structuring_eval.py`**: add `max_tokens` to `_redacted()`.
   Provenance-only; no metric arithmetic changes.
3. **`governance/eval_runner.py`** (new): `GenerationConfig`,
   `config_from_artifact`, `prepare_artifact`, `score_artifact`.
4. **`scripts/run_evaluation.py`** (new CLI).
5. **`scripts/run_structuring_eval.py`**: ingest through `score_artifact`.
6. **`db/schema.sql`**: the `created_at` comment.
7. **Tests**: `tests/test_eval_policy.py`, `tests/test_eval_runner.py`, the
   guard regression in `tests/test_evaluate.py`, and the stale
   `record_structuring_run` reference in `tests/test_structuring_eval.py`.
8. **Backfill** window 1 against a real database, then the ROADMAP entry.

---

## Three decisions the spec left to implementation

Recorded here rather than made quietly in code.

### 1. The split check is data-free, and deliberately not `verify_split()`

Spec §7 lists `SplitDriftError` from `governance/heldout.py::verify_split` as
the drift behavior. `verify_split()` rebuilds the split from `data/`, which is
gitignored and absent in CI, so calling it inside `score_artifact` would
contradict spec §8's requirement that the replay tests need neither
`needs_data` nor `needs_db`.

The two checks answer different questions:

| Check | Question | Needs `data/` |
|---|---|---|
| `verify_split()` | do the datasets on disk still reproduce the lock? | yes |
| artifact digest vs `locked_digest()` | was this artifact measured on the split we have locked? | no |

Ingesting a committed artifact never touches the datasets, so requiring them
would be wrong. `score_artifact` raises the same `SplitDriftError` type, on the
data-free comparison. `verify_split()` stays where it already is, in
`run_structuring_eval.py`, guarding *generation* before a single paid call.

### 2. The live path ingests its own artifact rather than assembling a row

Spec §9 says `run_structuring_eval.py` writes through `record_eval_run`. It
does so by calling `score_artifact` on the artifact it just wrote, so the live
run and the July backfill are the same code path. Two consequences worth
having: `replay()`'s recompute-and-cross-check now runs on every live run for
free, and there is exactly one place that knows how to turn a run into a row.

### 3. `_redacted()` gains `max_tokens`

Not in spec §9's file table, but required by spec §4 ("`GenerationConfig`
records all four fields on every row from now on") and §10.2 ("the first
fully-provenanced window is whichever run P3-2 performs"). The artifact is
where provenance lives; without this, `config_from_artifact` returns
`max_tokens=None` forever and P3-2's window is as unprovenanced as July's.

Backwards it stays `null`, per spec §4. Nothing infers 8000 for July.

---

## Test plan

Everything in spec §8, plus one the spec implies but does not list: **the
PriMock57 `accuracy` NULL must survive `record_eval_run`**, checked without a
database by asserting on the built row rather than only in the `needs_db`
round trip. Spec §7 calls this out as "the one behavior that needs its own
test", and a DB-free version of it runs in CI where the `needs_db` one does not.

## Verification

- `make lint` clean, `make test` green (325 passed / 1 xfailed going in).
- Backfill both July artifacts against a real Postgres, read the rows back, and
  confirm `created_at` is the July timestamp and ACI's `f1` is
  `0.8685633622463043`.
- Confirm a `coding` row with a non-NULL `f1` is refused **and inserts nothing**.
