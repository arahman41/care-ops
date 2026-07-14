"""Measure note-structuring accuracy on the frozen held-out set (P1-4).

    python scripts/run_structuring_eval.py --dataset aci
    python scripts/run_structuring_eval.py --dataset aci --limit 5 --no-db
    python scripts/run_structuring_eval.py --replay governance/eval_artifacts/<f>.json

The split is verified against the committed lock before a single API call is
made. If the datasets on disk no longer reproduce the split frozen in P0-5,
this refuses to run rather than emit a plausible number nobody can trust.

Every number printed here is measured. Nothing is hardcoded.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from governance.evaluate import record_structuring_run          # noqa: E402
from governance.heldout import (                                # noqa: E402
    ACI_DATASET_REF,
    SplitDriftError,
    load_aci_heldout,
    load_primock_heldout,
    verify_split,
)
from governance.llm_cache import Cache                          # noqa: E402
from governance.structuring_eval import (                       # noqa: E402
    AGENT_NAME,
    CACHE_DIR,
    evaluate_examples,
    evaluate_primock,
    locked_digest,
    replay,
    write_artifacts,
)


def _report(result) -> str:
    m = result.metrics
    c = result.counts

    if result.placement_scored:
        placement = (f"  placement accuracy        {m['accuracy']:.3f}   "
                     f"of what it captured")
        strict = result.strict_metrics
        # With no separable notes there is no strict subset. Printing 0.000
        # there would read as "scored terribly" rather than "not applicable".
        if result.strict_n:
            strict_line = (
                f"  {result.fused_notes} of {len(result.examples)} reference "
                f"notes fuse ASSESSMENT AND PLAN, so a\n  fact from those may "
                f"sit in either section and still count as placed.\n"
                f"  On the {result.strict_n} notes that separate them, strict "
                f"F1 = {strict['f1']:.3f},\n  strict placement accuracy = "
                f"{strict['accuracy']:.3f}.")
        else:
            strict_line = ("  Every reference note in this run fuses ASSESSMENT "
                           "AND PLAN, so there is\n  no strict subset to report.")
    else:
        placement = ("  placement accuracy        n/a     reference notes are "
                     "not SOAP-sectioned")
        strict_line = (
            "  PriMock57 reference notes are free-text GP shorthand, not SOAP\n"
            "  sections, so there is no ground truth for WHERE a fact belongs.\n"
            "  Placement is not scored and eval_runs.accuracy is written NULL\n"
            "  rather than filled with a 1.0 that would mean nothing.")

    highlights = ""
    if result.highlights_total:
        highlights = (
            f"\n  highlights recall         "
            f"{result.highlights_recall:.3f}   "
            f"{result.highlights_found}/{result.highlights_total} human-authored "
            f"key concepts captured")

    return f"""
================ NOTE-STRUCTURING ACCURACY (P1-4) ================
dataset          {result.dataset_ref}   n = {len(result.examples)}
split digest     {result.split_digest[:16]}...
structuring      {result.structuring_model} (effort: {result.structuring_effort})

  F1                        {m['f1']:.3f}   <- headline
  recall                    {m['recall']:.3f}   captured{' AND correctly placed' if result.placement_scored else ''}
  precision                 {m['precision']:.3f}   grounded in the transcript
{placement}
  hallucination rate        {m['hallucination_rate']:.3f}{highlights}

counts
  reference facts           {c.ref_facts}
    captured                {c.captured}
    correctly placed        {c.correctly_placed}
  generated facts           {c.gen_facts}
    supported by transcript {c.supported}

disclosure
{strict_line}

  recall is scored against the clinician note (the gold for what matters).
  precision is scored against the transcript (the gold for what is true),
  because the clinician note is a selective summary and omitting something
  is not the same as inventing it.
==================================================================
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["aci", "primock"], default="aci")
    parser.add_argument("--limit", type=int, default=None,
                        help="score only the first N encounters (smoke run)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--whisper", default="base",
                        help="Whisper model size for the PriMock57 audio path")
    parser.add_argument("--no-db", action="store_true",
                        help="skip the eval_runs write")
    parser.add_argument("--window-label", default="v1")
    parser.add_argument("--replay", type=Path, default=None,
                        help="recompute the metrics from a committed artifact")
    args = parser.parse_args()

    if args.replay:
        out = replay(args.replay)
        m = out["metrics"]
        print(f"\nReplayed {args.replay.name} with zero API calls.")
        print(f"  n           {out['payload']['n_examples']}")
        print(f"  F1          {m['f1']:.3f}")
        print(f"  recall      {m['recall']:.3f}")
        print(f"  precision   {m['precision']:.3f}")
        print(f"  placement   {m['accuracy']:.3f}")
        print("\nRecomputed from the per-fact verdicts and it matches the "
              "artifact.\n")
        return 0

    # The guard. Before anything is spent, before anything is scored.
    try:
        verify_split()
    except SplitDriftError as exc:
        print(f"\nREFUSING TO SCORE\n\n{exc}\n", file=sys.stderr)
        return 1

    is_primock = args.dataset == "primock"
    examples = load_primock_heldout() if is_primock else load_aci_heldout()

    if args.limit:
        examples = examples[:args.limit]
        print(f"SMOKE RUN: {args.limit} of the held-out set. Not a headline "
              f"number.")

    print(f"Scoring {len(examples)} held-out encounters with {args.workers} "
          f"workers. Cached calls are free; a cold run costs real money.")
    if is_primock:
        print(f"PriMock57 runs from audio: Whisper ({args.whisper}) on both "
              f"speaker tracks, merged by timestamp. This is slow on CPU.")
    print()

    done = [0]

    def progress(_result):
        done[0] += 1
        print(f"\r  {done[0]}/{len(examples)}", end="", flush=True)

    cache = Cache(CACHE_DIR)
    if is_primock:
        result = evaluate_primock(
            examples, cache=cache, model_size=args.whisper,
            workers=args.workers, on_done=progress)
    else:
        result = evaluate_examples(
            examples, cache=cache, workers=args.workers,
            dataset_ref=ACI_DATASET_REF, split_digest=locked_digest(),
            on_done=progress)
    print()

    print(_report(result))

    artifact = write_artifacts(result)
    print(f"artifact  {artifact.relative_to(REPO_ROOT)}")

    if args.limit:
        print("\nSmoke run: not writing an eval_runs row.")
        return 0

    if args.no_db:
        print("\n--no-db: not writing an eval_runs row.")
        return 0

    row_id = record_structuring_run(
        agent_name=AGENT_NAME,
        model=result.structuring_model,
        window_label=args.window_label,
        dataset_ref=result.dataset_ref,
        n_examples=len(result.examples),
        metrics=result.metrics,
    )
    print(f"eval_runs id = {row_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
