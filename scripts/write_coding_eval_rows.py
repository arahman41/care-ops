"""Write the two P2-4 eval_runs rows from a committed coding-benchmark artifact.

    python scripts/write_coding_eval_rows.py governance/eval_artifacts/coding_<stamp>.json

Exists because the benchmark and the database can be available at different
times: on Windows the Postgres container is started by hand, so a run made with
--no-db still produces a complete artifact that has nowhere to be written yet.
This closes that gap without re-running anything.

Zero API calls. The numbers come from the artifact's stored per-note tallies,
not from a re-run, so the rows carry the original run's latency and token
counts rather than the empty latency a warm-cache re-run would produce.

The write is gated on replay_coding() succeeding, so a row can only be written
from an artifact that recomputes to its own stored aggregates. A row that
disagrees with its artifact is therefore not reachable through this path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from governance.coding_benchmark import replay_coding   # noqa: E402
from governance.evaluate import record_coding_run       # noqa: E402

AGENT_NAME = "coding"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("artifact", type=Path,
                    help="committed coding_<stamp>.json (never the .full.json roster)")
    ap.add_argument("--window-label", default="v1")
    args = ap.parse_args()

    if args.artifact.name.endswith(".full.json"):
        print("Refusing: that is the gitignored roster, which carries billing "
              "codes. Pass the committed coding_<stamp>.json instead.",
              file=sys.stderr)
        return 1

    # Gate the write on the artifact reproducing its own numbers.
    recomputed = replay_coding(args.artifact)
    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    comparison = payload.get("comparison", {})
    dataset_ref = payload["dataset_ref"]

    print(f"\nReplayed {args.artifact.name}; every per-arm rate recomputes from "
          f"its stored tallies.")

    for arm, agg in recomputed.items():
        block = payload["arms"][arm]
        row_id = record_coding_run(
            agent_name=AGENT_NAME,
            model=block["requested_model"],
            model_effort=block["requested_effort"],
            window_label=args.window_label,
            dataset_ref=dataset_ref,
            n_examples=agg["n_notes"],
            metrics={**agg, "arm": arm, "comparison": comparison},
        )
        print(f"  eval_runs id = {row_id}  arm {arm}  "
              f"{block['requested_model']} at {block['requested_effort']}  "
              f"n={agg['n_notes']}  verified_rate={agg['verified_rate']}")

    print("\naccuracy/f1/precision/recall are NULL on both rows by construction; "
          "the verified rate lives in metrics (spec section 6).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
