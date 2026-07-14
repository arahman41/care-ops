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

## The harness verifies the split before it scores

`governance/heldout.py` recomputes the manifest from the datasets on disk and
compares it to the committed lock before a single API call is made. A mismatch
raises `SplitDriftError` and the run refuses to start. An accuracy computed
against a drifted split is not slightly wrong, it is meaningless while still
looking perfectly plausible on a resume, so this is a hard error and never a
warning.

## Auditing the judge (P1-4)

The headline structuring number is produced by a model (Haiku 4.5, temperature
0) grading another model. That is only defensible if a human has checked the
grader, so 30 verdicts were sampled at random from the committed artifact and
adjudicated by hand.

**Agreement: 29 / 30 (96.7%)**, on run `structuring_aci-bench-heldout-v1_20260714T032403Z`
(seed 20260713, sampled uniformly from a pool of 13,975 verdicts: 19 presence,
11 support). Reproduce the sample with that seed to re-audit the same 30.

The one disagreement, and it is recorded because its direction matters:

- **D2N178, presence.** Reference fact "intermittently completes at home blood
  pressure checks". The generated note says only that "her home blood pressure
  was 116 on the dot a couple of weeks ago", which is a single reading, not a
  statement that she checks intermittently. The judge marked it FOUND. That
  credits an implication, which its own rubric forbids, and it **inflates
  recall**. At 1 in 30 the effect is small, but it is an optimistic error, so
  the reported recall should be read as a mild upper bound.

Two things partially offset it, and both are disclosed rather than netted out:

- **Placement is understated by the reference notes' own idiosyncrasies.** In
  two sampled verdicts (D2N153, D2N168) the reference clinician filed an x-ray
  or EKG *result* under `PLAN`, while the model filed it under Assessment or
  Objective. The model earns no placement credit for what is arguably the more
  standard structuring. ACI-Bench section headers are the ground truth for
  placement, and they are not always the textbook answer.
- **The judge catches real hallucinations.** On D2N138 the note claimed the
  patient "last used nebulizer during a childhood bronchitis episode" when the
  transcript offers those as two alternatives ("the last time was when I had
  bronchitis, *or* a few select times when I was a kid"). The judge marked it
  unsupported. That is exactly the subtle fabrication precision exists to find.

Three further verdicts were borderline and were allowed to stand: "denies drug
use" (the patient was asked about tobacco, drugs and alcohol and answered only
about wine, so the denial is by omission), "EKG is unremarkable" against the
note's "no signs of a heart attack", and "swelling in knees" against "knees
appear a little inflamed".

## One PriMock57 highlight is excluded from the denominator (P1-4)

PriMock57's held-out consultations carry 30 human-authored `highlights` in the
raw dataset. **29 are scored.** `day4_consultation04` has exactly one highlight
and it is an empty string.

A blank annotation is a key concept the annotator never wrote, not one the model
failed to capture. Left in, it would sit in the highlights-recall denominator as
a phantom that nothing could ever satisfy, docking the Phase 1 gate metric for a
fact that does not exist. It is filtered in `load_primock_heldout()`, and
`judge_presence` now refuses a blank fact outright rather than asking a model to
grade nothing.

**This exclusion is in our favour, which is exactly why it is written down
here.** Reported highlights recall is **26 / 29 = 0.897**. Had the blank stayed
in the denominator it would read 26 / 30 = 0.867. Dropping it raises the gate
metric by three points, so it is precisely the kind of adjustment that should
never be applied quietly: the justification has to stand on its own, and a
reader has to be able to reverse it. The justification is that an empty string
is not a key concept, and a model cannot capture something the annotator never
wrote. If you disagree, 26 / 30 is the number.
