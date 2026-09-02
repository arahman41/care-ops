"""Compare two structuring windows and report whether the move is drift (P3-3).

    python scripts/run_drift_check.py \
        --reference governance/eval_artifacts/structuring_aci-bench-heldout-v1_20260714T032403Z.json \
        --current   governance/eval_artifacts/structuring_aci-bench-heldout-v1_20260831T205449Z.json

    python scripts/run_drift_check.py --reference-run 7 --current-run 25

Zero API calls and zero cost: the comparison is a paired bootstrap over the
artifacts' per-fact verdicts. The --run form reads eval_runs only as an INDEX,
to resolve a row id to the artifact it was filed from, so the statistics never
depend on a database being up.

Exit codes, so CI can branch without parsing prose:

    0  NO_DRIFT
    1  DRIFT
    2  NOT_ATTRIBUTABLE or NOT_COMPARABLE

Note that 2 is not an error. It is the honest answer for the only real pair
this project currently has, and it means the delta exists but may not be
assigned to the model. See the caveats the run prints.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from governance.drift import (                                   # noqa: E402
    DRIFT_REPLICATES,
    DriftVerdict,
    compare_structuring_windows,
)

ARTIFACT_DIR = REPO_ROOT / "governance" / "eval_artifacts"

EXIT_CODES = {
    DriftVerdict.NO_DRIFT: 0,
    DriftVerdict.DRIFT: 1,
    DriftVerdict.NOT_ATTRIBUTABLE: 2,
    DriftVerdict.NOT_COMPARABLE: 2,
}


def _artifact_for_run(run_id: int) -> Path:
    """Resolve an eval_runs id to the artifact it was filed from.

    The database is an index here and nothing more. Every number this script
    prints is recomputed from the artifact, so a stale or edited row cannot
    change a verdict.
    """
    from shared.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT metrics FROM eval_runs WHERE id = %s", (run_id,)).fetchone()
    if not row:
        raise SystemExit(f"eval_runs has no row {run_id}")

    name = ((row[0] or {}).get("provenance", {}) or {}).get("artifact")
    if not name:
        raise SystemExit(
            f"eval_runs row {run_id} records no provenance.artifact, so there "
            f"is nothing to recompute from. Rows filed before P3-1 carry no "
            f"provenance; re-file it with scripts/refile_eval_run.py")
    return ARTIFACT_DIR / name


def _resolve(path: str | None, run_id: int | None, side: str) -> Path:
    if path:
        return Path(path)
    if run_id is not None:
        return _artifact_for_run(run_id)
    raise SystemExit(f"give either --{side} or --{side}-run")


def _num(value: float | None, places: int = 6) -> str:
    return "n/a" if value is None else f"{value:+.{places}f}"


def _report(result, reference: Path, current: Path) -> str:
    lines = [
        "",
        "==================== DRIFT CHECK (P3-3) ====================",
        f"verdict       {result.verdict.value.upper()}",
        f"metric        {result.metric}",
        f"reference     {reference.name}",
        f"current       {current.name}",
        "",
    ]

    if result.delta is None:
        lines.append("  nothing was computed: the two windows are not two "
                     "measurements of one thing.")
    else:
        lines += [
            f"  {result.metric:<12}{result.reference:.6f} -> "
            f"{result.current:.6f}",
            f"  delta       {_num(result.delta)}",
            f"  95% BCa CI  [{_num(result.ci[0])}, {_num(result.ci[1])}]",
            f"  detectable  {result.mde:.6f}   (half-width, n={result.n_paired})",
            f"  direction   {result.direction or 'none'}",
        ]
        if result.bootstrap is not None:
            boot = result.bootstrap
            lines.append(
                f"  bootstrap   seed {boot.seed}, {boot.retained} retained, "
                f"{boot.dropped} dropped")

    if result.comparability:
        lines.append(f"\n  config differs on: {', '.join(result.comparability)}")
    if result.unmeasured_variance:
        lines.append(
            f"  variance not in the interval: "
            f"{', '.join(result.unmeasured_variance)}")

    lines.append("\n  read this before quoting the number above:")
    for caveat in result.caveats:
        lines.append(f"    - {caveat}")

    if result.verdict is DriftVerdict.NOT_ATTRIBUTABLE:
        lines.append(
            "\n  NOT_ATTRIBUTABLE is the module working, not failing. The "
            "delta is measured;\n  what it cannot do is assign it to the "
            "model.")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference")
    parser.add_argument("--current")
    parser.add_argument("--reference-run", type=int)
    parser.add_argument("--current-run", type=int)
    parser.add_argument("--metric", default="f1")
    parser.add_argument("--replicates", type=int, default=DRIFT_REPLICATES)
    args = parser.parse_args()

    reference = _resolve(args.reference, args.reference_run, "reference")
    current = _resolve(args.current, args.current_run, "current")

    result = compare_structuring_windows(
        json.loads(reference.read_text(encoding="utf-8")),
        json.loads(current.read_text(encoding="utf-8")),
        metric=args.metric, replicates=args.replicates)

    print(_report(result, reference, current))
    return EXIT_CODES[result.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
