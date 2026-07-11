# Held-Out Split Policy

The held-out split is the measurement instrument for this project. Every
headline accuracy and drift number is computed against it. If it is ever
used to make the system look better, every number it produces becomes
meaningless. This document defines the split and the rules that keep it
trustworthy. It is a hard rule, referenced by CLAUDE.md and AGENTS.md.

## What the split is

Every labeled encounter is assigned to exactly one of three splits by
`shared/splits.py`. The assignment is frozen in the committed lock file
`scripts/heldout_split.lock.json` and reproduced deterministically by
`scripts/build_heldout_split.py`.

- **train**: available for any purpose, including a future fine-tune (P5-1).
- **dev**: the only surface where tuning is allowed. Look here, iterate
  here, read errors here, choose prompts, rules, thresholds, and few-shot
  examples here.
- **heldout**: frozen. Used only to measure. Never inspected to change the
  system.

Current assignment (from the committed lock, salt `care-ops-copilot/heldout/v1`):

| Dataset    | train | dev | heldout | total |
|------------|-------|-----|---------|-------|
| primock57  | 35    | 15  | 7       | 57    |
| aci-bench  | 67    | 20  | 120     | 207   |
| **total**  | 102   | 35  | 127     | 264   |

PriMock57 is the audio source, so its 7 held-out consultations back the
audio-in demo and latency measurement. ACI-Bench is text only and supplies
the large-N held-out set (120) for note-structuring accuracy.

## The tuning-forbidden rule

The held-out split must never influence any modeling decision. In concrete
terms, do not:

- read held-out inputs, references, or per-example errors to decide how to
  change a prompt, rule, threshold, model, or few-shot example,
- select a model or effort level because it scores better on held-out,
- add held-out examples to a prompt or a fine-tune,
- run repeated held-out evaluations and keep the configuration that wins,
- move an encounter between splits to change a number.

All iteration happens on **dev**. The held-out split is touched only to
produce a final measured number that is written to `eval_runs` and reported
as is, including when it is worse than hoped.

## Why the split is leak-free

1. **Grouping.** Assignment keys on the encounter group, never a single
   file. A PriMock57 consultation owns a doctor file and a patient file;
   `primock_group_id` normalizes both to one id, so they cannot land in
   different splits. ACI-Bench official splits are disjoint by
   `encounter_id`, verified in tests.
2. **Determinism.** PriMock57 uses `sha256(SALT + group_id) % 100`
   bucketing. sha256 is identical across processes and operating systems,
   unlike Python's builtin `hash()`, which is per-process salted and would
   silently leak data over time. The assignment sees only the id string,
   never the content, so it cannot be biased by what is in an encounter.
3. **Honor the benchmark.** ACI-Bench ships an official train/valid/test
   split. We use it unchanged. Its three test sets are our held-out, which
   also keeps results comparable to published ACI-Bench numbers.

## Reproduce and verify

```
# Rebuild the working manifest (data/splits/heldout_manifest.csv, gitignored)
python scripts/build_heldout_split.py

# Assert the on-disk split still matches the committed lock (use in CI)
python scripts/build_heldout_split.py --verify
```

`--verify` exits non-zero if the split drifts from the lock. The lock holds
identifiers only, never clinical text, so it is safe to commit while all
dataset content under `data/` stays gitignored.

## Changing the split

Redrawing the split is a deliberate, logged event, never a routine edit.
To redraw, bump `SALT` in `shared/splits.py` to `v2`, re-run with
`--write-lock`, and record why in the commit message. Never adjust
thresholds or move encounters to chase a metric.
