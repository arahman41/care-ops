"""Build, freeze, and verify the leak-free held-out split.

Usage:
  python scripts/build_heldout_split.py            # build + write local manifest
  python scripts/build_heldout_split.py --write-lock   # also freeze the lock
  python scripts/build_heldout_split.py --verify       # check disk matches lock

The split logic lives in shared/splits.py. This script is the operational
entry point: it reads the local datasets, writes the working manifest under
data/splits/ (gitignored, never committed), and maintains the committed lock
file scripts/heldout_split.lock.json so the split is reproducible and
tamper-evident in CI without shipping any clinical content. Ids are not PHI.

The held-out split is tuning-forbidden. See docs/HELD-OUT-POLICY.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Allow running as a plain script (python scripts/build_heldout_split.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.splits import (  # noqa: E402
    ACI_OFFICIAL_TO_SPLIT,
    SALT,
    SplitRecord,
    _DEV_MAX,
    _HELDOUT_MAX,
    build_all_records,
    manifest_digest,
    split_counts,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"
MANIFEST_PATH = DATA_ROOT / "splits" / "heldout_manifest.csv"
LOCK_PATH = REPO_ROOT / "scripts" / "heldout_split.lock.json"


def _write_manifest(records: list[SplitRecord]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["dataset", "encounter_id", "split"])
        for r in records:
            writer.writerow([r.dataset, r.encounter_id, r.split])


def _lock_payload(records: list[SplitRecord]) -> dict:
    return {
        "salt": SALT,
        "thresholds": {"heldout_max": _HELDOUT_MAX, "dev_max": _DEV_MAX},
        "aci_official_to_split": ACI_OFFICIAL_TO_SPLIT,
        "counts": split_counts(records),
        "digest": manifest_digest(records),
        "records": [
            {"dataset": r.dataset, "encounter_id": r.encounter_id,
             "split": r.split}
            for r in records
        ],
    }


def _write_lock(records: list[SplitRecord]) -> None:
    with LOCK_PATH.open("w", encoding="utf-8") as fh:
        json.dump(_lock_payload(records), fh, indent=2, sort_keys=True)
        fh.write("\n")


def _print_summary(records: list[SplitRecord]) -> None:
    counts = split_counts(records)
    print(f"salt: {SALT}")
    print(f"total encounters: {len(records)}")
    print(f"digest: {manifest_digest(records)}")
    for dataset in sorted(counts):
        c = counts[dataset]
        print(f"  {dataset}: train={c['train']} dev={c['dev']} "
              f"heldout={c['heldout']}")


def _verify(records: list[SplitRecord]) -> int:
    if not LOCK_PATH.exists():
        print("VERIFY FAILED: no lock file. Run --write-lock first.")
        return 1
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    rebuilt = manifest_digest(records)
    if rebuilt != lock["digest"]:
        print("VERIFY FAILED: split drifted from the committed lock.")
        print(f"  lock digest:    {lock['digest']}")
        print(f"  rebuilt digest: {rebuilt}")
        print("If this change is intentional, bump SALT and re-freeze.")
        return 1
    print(f"VERIFY OK: split matches lock ({len(records)} encounters).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-lock", action="store_true",
                        help="freeze the current split into the committed lock")
    parser.add_argument("--verify", action="store_true",
                        help="assert the on-disk split matches the lock")
    args = parser.parse_args()

    if not DATA_ROOT.exists():
        print("No data/ directory. See scripts/download_data.md.")
        return 1

    records = build_all_records(DATA_ROOT)

    if args.verify:
        return _verify(records)

    _write_manifest(records)
    print(f"wrote manifest: {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    if args.write_lock:
        _write_lock(records)
        print(f"wrote lock: {LOCK_PATH.relative_to(REPO_ROOT)}")
    _print_summary(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
