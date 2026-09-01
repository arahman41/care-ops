# P3-2: Two Windows of Data, implementation plan

**Spec:** `docs/superpowers/specs/2026-08-31-p3-2-two-windows-design.md`
**Branch:** `p3-2-two-windows`, stacked on `p3-1-evaluation-runner` (PR #9)
**Model/effort:** Opus at max, against the guide's own S5/medium row. The
spec's section 0 argues that row is wrong; the guide is corrected in this task.

---

## Branch note, read before merging

This branch is stacked on `p3-1-evaluation-runner` because P3-2 builds directly
on P3-1's guarded writer. Per the P2-6 incident recorded in ROADMAP P2-7:
**do not `gh pr merge --delete-branch` on PR #9 while this branch's PR is
open.** Deleting a branch that is another open PR's base auto-closes the second
PR, and GitHub then refuses both to reopen it and to retarget it. Merge #9
without `--delete-branch`, or merge this one first.

---

## Order of work

1. **`governance/pricing.json`**: correct Sonnet 5 to $2/$10, add Haiku 4.5 at
   $1/$5. Both rates read off the published table, not recalled.
2. **`shared/llm.py`**: `UsageRecorder` and `recording_usage()`, hooked into
   `call_detailed` so every call site gets accounting without changing.
3. **`governance/llm_cache.py`**: per-task hit and miss counters on `Cache`.
4. **`governance/structuring_eval.py`**: `window_cache()`, run accounting on
   `RunResult`, both written into the artifact.
5. **`governance/evaluate.py`**: `windows_with_counts()`.
6. **`governance/eval_runner.py`**: both guards.
7. **`scripts/run_structuring_eval.py`**: `--cache-namespace`, cost report.
8. **`tests/test_eval_windows.py`**, plus fixture repairs in
   `tests/test_eval_runner.py`.
9. **Pilot**, report, then the full run only on approval.

---

## Decisions made during implementation

### 1. Namespacing by directory, not by folding the window into the cache key

Folding the label into `cache_key` would also work and is a smaller diff, but
it orphans the existing flat cache, which holds P2-4's coding entries as well
as the structuring ones. This project has already lost a run to a cache key
changed underneath it. Directory namespacing is non-destructive: window 1 stays
at the root, later windows get their own directory, and nothing is migrated.

### 2. Transcription is shared, every hosted call is window-scoped

Whisper runs locally, is not the model under test, and the audio never changes,
so re-transcribing costs two hours of CPU and buys nothing. Everything downstream
of it is window-scoped so the window stays a coherent snapshot rather than a
blend of two experiments. Reference decomposition is re-paid under this rule
(about 480 Haiku calls) even though it is arguably shareable, because a rule
with a cost-motivated exception carved into it stops being a rule.

### 3. Guard 2 lives in `record_scored`, not `record_eval_run`

`record_eval_run` is P3-1's single guarded writer and stays the low-level
primitive. Guard 2 needs the prepared `ScoredArtifact` (it compares provenance
counts), so it sits one level up. A test that wants to exercise the writer
alone can still call `record_eval_run` directly.

### 4. Guard 1 exempts artifacts with no `cache_stats`

Everything written before this task, window 1 included, carries none. Treating
absence as a replay would make the real July windows unfileable, and absence of
evidence is not evidence of a replay.

### 5. Guard 1 targets all-hits-with-no-misses, not any cache use

A run resumed after a crash is partly warm and entirely legitimate. The
signature being detected is specifically "nothing was generated".

---

## What the tests had to change, and why that was the guard working

Three P3-1 tests and three new ones failed on the first full run because they
filed the committed July artifact under a fresh window label. That is exactly
the duplicate guard 2 exists to refuse, so the guard was right and the fixtures
were wrong.

- `tests/test_eval_windows.py` now builds **synthetic** artifacts from a
  `RunResult`, so a test window is a genuinely different measurement rather
  than a relabelled copy of the real one.
- `tests/test_eval_runner.py`'s two round trips now file under `v1`, the label
  rows 7 and 8 already use, and clean up **by row id** rather than by label,
  since deleting by label would destroy the real windows.

## Verification

- `make lint` clean, `make test` green, and `eval_runs` still holding exactly
  rows 3, 4, 7, 8 afterwards, confirmed by direct query.
- Mutation check: `test_guard_1_actually_detects_the_replay_signature`.
- Pilot on 5 held-out encounters in its own namespace, with measured tokens and
  cost, extrapolated to 120 and reported before any full run.
