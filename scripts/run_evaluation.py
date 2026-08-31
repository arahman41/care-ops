"""File a committed eval artifact as one window in eval_runs (P3-1).

    python scripts/run_evaluation.py --agent note_structuring \
        --artifact governance/eval_artifacts/structuring_aci-bench-heldout-v1_20260714T032403Z.json \
        --window 2026-07-w2

    python scripts/run_evaluation.py --agent coding --artifact <any> --window w
        -> refused by name, with the reason

Zero API calls and zero cost: the metrics are recomputed from the artifact's
per-fact verdicts, not regenerated. Use scripts/run_structuring_eval.py to
produce a NEW measurement, which costs real money; use this to file one that
already exists.

--no-db prints the row without writing it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from governance.evaluate import (                                # noqa: E402
    SCOREABLE,
    UNSCOREABLE,
    EvalPolicyError,
)
from governance.eval_runner import (                             # noqa: E402
    prepare_artifact,
    record_scored,
)
from governance.heldout import SplitDriftError                   # noqa: E402


def _report(scored) -> str:
    m = scored.metrics
    c = scored.config

    def num(name: str) -> str:
        value = m.get(name)
        # None is a metric the run DECLINED, not one that is missing. Printing
        # 0.000 would read as "scored terribly" for exactly the case the
        # harness refused to score at all.
        return f"{value:.3f}" if value is not None else "n/a  (not scorable)"

    cap = (str(c.max_tokens) if c.max_tokens is not None
           else "not recorded by that harness (see P3-1 spec section 4)")

    return f"""
==================== EVAL RUN (P3-1) ====================
agent         {scored.agent_name}
window        {scored.window_label}
dataset       {scored.dataset_ref}   n = {scored.n_examples}
measured at   {scored.measured_at.isoformat()}

  f1          {num('f1')}   <- headline
  recall      {num('recall')}
  precision   {num('precision')}
  accuracy    {num('accuracy')}   section placement

generation configuration
  model       {c.model}
  effort      {c.effort}
  prompt      {c.prompt_hash}
  max_tokens  {cap}

  Recomputed from the per-fact verdicts and it matches the artifact.
=========================================================
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True,
                        help=f"scoreable: {sorted(SCOREABLE)}; "
                             f"refused: {sorted(UNSCOREABLE)}")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--window", required=True,
                        help="window label, e.g. 2026-07-w2")
    parser.add_argument("--no-db", action="store_true",
                        help="print the row without writing it")
    args = parser.parse_args()

    try:
        scored = prepare_artifact(agent_name=args.agent,
                                  artifact_path=args.artifact,
                                  window_label=args.window)
    except EvalPolicyError as exc:
        print(f"\nREFUSING TO SCORE\n\n{exc}\n", file=sys.stderr)
        return 1
    except SplitDriftError as exc:
        print(f"\nREFUSING TO FILE\n\n{exc}\n", file=sys.stderr)
        return 1

    print(_report(scored))

    if args.no_db:
        print("--no-db: not writing an eval_runs row.")
        return 0

    print(f"eval_runs id = {record_scored(scored)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
