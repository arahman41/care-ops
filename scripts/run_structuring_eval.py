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

from governance.eval_runner import score_artifact               # noqa: E402
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
    ARTIFACT_DIR,
    CACHE_DIR,
    SMOKE_ARTIFACT_DIR,
    evaluate_examples,
    evaluate_primock,
    locked_digest,
    replay,
    window_cache,
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


def _cost_report(result, full_n: int | None = None) -> str:
    """What the run actually cost, and what the full set would cost (P3-2).

    The extrapolation is the point of a pilot. P2-4 learned that effort-driven
    reasoning tokens are billed as output and are invisible in the cached
    response text, so per-note cost cannot be estimated from the prompt; it has
    to be measured on real notes and scaled.
    """
    window = (result.cache_stats or {}).get("window", {})
    hits, misses = window.get("hits", {}), window.get("misses", {})
    n = len(result.examples)

    tasks = sorted(set(hits) | set(misses))
    cache_lines = "\n".join(
        f"  {task:12} {misses.get(task, 0):5} generated  {hits.get(task, 0):5} from cache"
        for task in tasks) or "  (no cached tasks recorded)"

    usage_lines = "\n".join(
        f"  {model:28} {u['calls']:5} calls  {u['input_tokens']:9,} in  "
        f"{u['output_tokens']:9,} out"
        for model, u in sorted(result.usage.items())) or "  (no calls made)"

    if result.cost_usd is None:
        cost = ("  cost            not priceable: a model in this run is absent "
                "from governance/pricing.json,\n                  and a partial "
                "total would understate it")
    else:
        cost = f"  cost            ${result.cost_usd:.2f} for {n} encounters"
        if n:
            cost += f"  (${result.cost_usd / n:.4f} each)"

    extrapolation = ""
    if result.cost_usd is not None and full_n and n and full_n != n:
        projected = result.cost_usd / n * full_n
        extrapolation = (
            f"\n\n  PILOT EXTRAPOLATION\n"
            f"  {n} of {full_n} encounters measured. The full set projects to "
            f"about ${projected:.2f}.\n"
            f"  Sampling noise is real at n={n}: treat this as an order of "
            f"magnitude, not a quote.")

    return f"""
======================= RUN COST (P3-2) =======================
cache namespace activity  (generated = a real API call was made)
{cache_lines}

tokens
{usage_lines}

{cost}{extrapolation}
===============================================================
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
    parser.add_argument(
        "--cache-namespace", default=None,
        help="cache namespace for this run, defaulting to the window label. "
             "A NEW window must use a namespace of its own: the cache key "
             "does not cover the window, so reusing another window's "
             "namespace replays its notes and reproduces its metrics exactly. "
             "Pass 'legacy' to read the pre-P3-2 flat cache, which is where "
             "window 1 lives.")
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
        # None where the reference notes are not SOAP-sectioned. Printing the
        # recomputed 1.0 there would republish the number the run declined.
        placement = m["accuracy"]
        print(f"  placement   {placement:.3f}" if placement is not None
              else "  placement   n/a   reference notes are not SOAP-sectioned")
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
    full_n = len(examples)

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

    namespace = args.cache_namespace or args.window_label
    # 'legacy' is the pre-P3-2 flat cache, which is where window 1's outputs
    # live. Every other namespace gets its own directory, so a new window
    # cannot be served from an older one's notes.
    cache = Cache(CACHE_DIR) if namespace == "legacy" else window_cache(namespace)
    print(f"cache namespace  {namespace}  ({cache.root})")
    if is_primock:
        result = evaluate_primock(
            examples, cache=cache, model_size=args.whisper,
            workers=args.workers, on_done=progress,
            # Whisper is local, deterministic, and not the model under test,
            # so transcripts are shared across windows rather than re-run.
            transcribe_cache=Cache(CACHE_DIR))
    else:
        result = evaluate_examples(
            examples, cache=cache, workers=args.workers,
            dataset_ref=ACI_DATASET_REF, split_digest=locked_digest(),
            on_done=progress)
    print()

    print(_report(result))
    print(_cost_report(result, full_n=full_n))

    # A partial run is not a window, so its artifact does not go where windows
    # live. See SMOKE_ARTIFACT_DIR.
    artifact = write_artifacts(
        result, out_dir=SMOKE_ARTIFACT_DIR if args.limit else ARTIFACT_DIR)
    print(f"artifact  {artifact.relative_to(REPO_ROOT)}")

    if args.limit:
        print("\nSmoke run: not writing an eval_runs row.")
        return 0

    if args.no_db:
        print("\n--no-db: not writing an eval_runs row.")
        return 0

    # Ingest through the artifact that was just written, rather than building
    # a row from the in-memory result. Two things fall out of that. The live
    # path and the P3-1 backfill become the same code, so there is one way a
    # measurement becomes a row and one place the accuracy-family guard sits.
    # And replay()'s recompute-and-cross-check now runs on every live run for
    # free, which means a run whose stored metrics do not reproduce from its
    # own verdicts fails here rather than being filed as a window.
    row_id = score_artifact(agent_name=AGENT_NAME, artifact_path=artifact,
                            window_label=args.window_label)
    print(f"eval_runs id = {row_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
