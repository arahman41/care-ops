"""Measure |FY2025 \\ FY2026| for the P2-4 cause-2 floor decision (spec §5a).

The only codes a prior pin can rescue are those in FY2025 AND deleted from
FY2026. Usage:

    python scripts/measure_fy2025_diff.py --fy2025 /path/to/icd10cm_codes_2025.txt

Download and extraction are a human step: the CMS HTML pages 403 automated
fetchers while the direct file URLs return 200 (see data/vocab/PROVENANCE.md).
FY2025 has an original AND a mid-year update; record which one. Do NOT parse the
icd10cm_order_*.txt sibling: its lines lead with a sequence number and a
first-token parse loads sequence numbers as codes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from shared import vocab   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fy2025", type=Path, required=True,
                    help="extracted FY2025 icd10cm_codes_*.txt (NOT the order file)")
    args = ap.parse_args()

    fy2025 = {vocab.normalize(line.split(None, 1)[0])
              for line in args.fy2025.read_text(encoding="utf-8").splitlines()
              if line.strip()}
    fy2026 = vocab.load_icd10()

    only_2025 = fy2025 - fy2026
    print(f"FY2025 codes:            {len(fy2025)}")
    print(f"FY2026 codes (vendored): {len(fy2026)}")
    print(f"|FY2025 \\ FY2026|:       {len(only_2025)}   "
          f"(codes a prior pin could rescue)")
    print(f"|FY2026 \\ FY2025|:       {len(fy2026 - fy2025)}   "
          f"(additive churn, irrelevant to the floor)")
    if only_2025:
        print("Sample deleted codes:", sorted(only_2025)[:20])
    print()
    print("Decision rule (spec §5a): if |FY2025 \\ FY2026| is in the tens against "
          "~18 not-found events per arm, it cannot move the attribution. Drop the "
          "vendoring and record vocab_floor_version = 'none'. Otherwise vendor "
          "FY2025 with a dated release, the order-file trap avoided, and the "
          "member path recorded, and pass its normalized delta as floor_members "
          "to coding_metrics.floor_band. VOCAB_VERSION does NOT change (spec §5a).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
