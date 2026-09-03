"""P4-5: regenerate every published headline number and report what backs it.

    python scripts/audit_metrics.py                 # artifacts only, no infra
    python scripts/audit_metrics.py --with-suite    # also re-run the suite

Zero API calls and zero cost. Every number is recomputed from committed
evidence, never read back from the place it was published.

Exit codes:

    0  every checkable claim verified (skipped environment claims are
       reported, and do not pass silently)
    1  at least one published number could not be reproduced, or nothing
       backs it at all

See governance/audit.py for the claim manifest and for what each backing
tier actually proves.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from governance.audit import (                                   # noqa: E402
    Backing, CLAIMS, Status, audit, failed, summarize,
)

_MARK = {
    Status.VERIFIED: "ok",
    Status.CONSISTENT: "rec",
    Status.SKIPPED: "skip",
    Status.MISMATCH: "FAIL",
    Status.UNBACKED: "FAIL",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-suite", action="store_true",
                        help="also run pytest with coverage (slow, needs a "
                             "live Postgres or it reports SKIPPED)")
    args = parser.parse_args()

    findings, problems = audit(with_suite=args.with_suite)

    print("\n==================== METRIC AUDIT (P4-5) ====================\n")
    width = max(len(f.claim.key) for f in findings)
    for backing in Backing:
        rows = [f for f in findings if f.claim.backing is backing]
        if not rows:
            continue
        print(f"  [{backing.value}]")
        for finding in rows:
            claim = finding.claim
            shown = finding.regenerated or "-"
            line = (f"    {_MARK[finding.status]:>4}  {claim.key:<{width}}  "
                    f"published {claim.published!r}")
            if finding.status is Status.VERIFIED:
                line += "  regenerated identically"
            elif finding.status is Status.CONSISTENT:
                line += f"  {finding.detail}"
            elif finding.status is Status.SKIPPED:
                line += f"  {finding.detail}"
            else:
                line += f"  <- {finding.detail} (got {shown})"
            print(line)
        print()

    counts = summarize(findings)
    print(f"  {counts[Status.VERIFIED]} verified by regeneration, "
          f"{counts[Status.CONSISTENT]} observations cross-checked against "
          f"their record,")
    print(f"  {counts[Status.SKIPPED]} skipped for want of infrastructure, "
          f"{counts[Status.MISMATCH] + counts[Status.UNBACKED]} failed, "
          f"{len(CLAIMS)} claims total.")

    if problems:
        print("\n  could not regenerate:")
        for problem in problems:
            print(f"    - {problem}")

    bad = failed(findings)
    if bad:
        print(f"\n  AUDIT FAILED. {len(bad)} published number(s) are not "
              f"backed by what this repo can reproduce:")
        for finding in bad:
            print(f"    - {finding.claim.key}: {finding.detail}")
        print()
        return 1

    if counts[Status.SKIPPED]:
        print("\n  Every claim that could be checked here passed. The skipped "
              "ones need\n  live infrastructure; run with --with-suite, or "
              "with a cluster up, to close them.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
