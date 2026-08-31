"""Re-file an existing eval_runs row through the P3-1 guarded writer.

    python scripts/refile_eval_run.py --row-id 1 --agent note_structuring \
        --artifact governance/eval_artifacts/structuring_...json --dry-run

Why this exists. P1-4's July rows were written by record_structuring_run,
which P3-1 deleted. They carry the right numbers but no generation
provenance: model_effort and metrics are both NULL, so nothing records the
model, effort, prompt hash or output cap the measurement ran under. P3-3
compares two windows by their GenerationConfig, and a reference window with
no config is not comparable to anything.

This replaces such a row with the same measurement, re-filed from its own
committed artifact so it goes through the one guarded writer and arrives
carrying its provenance.

It is deliberately hard to misuse:

  - it REFUSES unless the artifact's replayed metrics reproduce the stored
    accuracy family, so a row is only ever deleted once it has been proved
    reproducible from committed evidence;
  - it REFUSES unless the artifact's own created_at is close to the row's, so
    a row cannot be paired with the wrong run's artifact;
  - it snapshots the old row to JSON before touching anything;
  - it INSERTS the replacement and verifies it BEFORE deleting the original,
    so an interrupted run leaves a visible duplicate rather than a missing
    measurement.

window_label and created_at are carried over unchanged. The measurement is
not re-run and nothing about it changes; only its provenance is filled in.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from governance.eval_runner import prepare_artifact, record_scored   # noqa: E402
from shared.db import get_conn                                       # noqa: E402

# eval_runs.accuracy and friends are REAL (single precision), so a stored
# value is the float32 image of the computed one. Comparing tighter than this
# would test the column's width rather than the metric.
REL_TOLERANCE = 1e-6

# The row is written moments after its artifact. A gap larger than this means
# the row and the artifact are not the same run.
MAX_CLOCK_GAP = timedelta(minutes=5)

FAMILY = ("accuracy", "f1", "precision", "recall")
COLUMNS = ("id", "agent_name", "model", "model_effort", "window_label",
           "dataset_ref", "n_examples", *FAMILY, "metrics", "created_at")


def _read_row(row_id: int) -> dict | None:
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM eval_runs WHERE id = %s",
            (row_id,))
        found = cur.fetchone()
        return dict(zip([c.name for c in cur.description], found)) if found else None


def _agrees(stored, replayed) -> bool:
    if stored is None or replayed is None:
        return stored is None and replayed is None
    return abs(stored - replayed) <= REL_TOLERANCE * max(abs(replayed), 1e-12)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--row-id", type=int, required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="verify and print, change nothing")
    args = parser.parse_args()

    old = _read_row(args.row_id)
    if old is None:
        print(f"no eval_runs row with id {args.row_id}", file=sys.stderr)
        return 1

    scored = prepare_artifact(agent_name=args.agent,
                              artifact_path=args.artifact,
                              window_label=old["window_label"])

    # ---- refuse unless this artifact demonstrably produced this row ----
    problems: list[str] = []
    if scored.dataset_ref != old["dataset_ref"]:
        problems.append(f"dataset_ref: row {old['dataset_ref']!r}, "
                        f"artifact {scored.dataset_ref!r}")
    if scored.n_examples != old["n_examples"]:
        problems.append(f"n_examples: row {old['n_examples']}, "
                        f"artifact {scored.n_examples}")
    for name in FAMILY:
        if not _agrees(old[name], scored.metrics.get(name)):
            problems.append(f"{name}: row {old[name]!r}, "
                            f"artifact {scored.metrics.get(name)!r}")
    gap = abs(scored.measured_at - old["created_at"])
    if gap > MAX_CLOCK_GAP:
        problems.append(f"created_at differs by {gap}, more than {MAX_CLOCK_GAP}")

    print(f"\nrow {args.row_id}  {old['agent_name']}  {old['dataset_ref']}  "
          f"window {old['window_label']}")
    print(f"  artifact      {args.artifact.name}")
    # The two differ by the lag between writing the artifact and inserting the
    # row. The artifact's stamp is the one that survives, because created_at
    # means when the MEASUREMENT was taken (db/schema.sql), and the artifact is
    # the measurement. The row's insert time is not a fact about the model.
    print(f"  row  created_at   {old['created_at'].isoformat()}  (insert time)")
    print(f"  artifact created_at {scored.measured_at.isoformat()}  (kept)")
    print(f"  clock gap     {gap}")
    for name in FAMILY:
        print(f"  {name:10}    stored={old[name]!r}  "
              f"replayed={scored.metrics.get(name)!r}")
    print(f"  adds          model_effort={scored.model_effort!r}, "
          f"metrics with provenance")

    if problems:
        print("\nREFUSING TO RE-FILE. This artifact did not produce this row:",
              file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("", file=sys.stderr)
        return 1

    print("\n  Every stored metric reproduces from the artifact's per-fact "
          "verdicts.")

    if args.dry_run:
        print("  --dry-run: nothing changed.\n")
        return 0

    # ---- snapshot, then insert-verify-delete ----
    snapshot_dir = REPO_ROOT / "governance" / "eval_artifacts" / "refiled"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_dir / f"eval_run_{args.row_id}_before_refile.json"
    snapshot.write_text(
        json.dumps({k: (v.isoformat() if k == "created_at" else v)
                    for k, v in old.items()}, indent=2, sort_keys=True),
        encoding="utf-8")
    print(f"  snapshot      {snapshot.relative_to(REPO_ROOT)}")

    new_id = record_scored(scored)
    new = _read_row(new_id)
    # The new stamp must be the artifact's exactly, and must still land in the
    # same moment as the original insert. Exact equality with the OLD row would
    # be wrong: they legitimately differ by the artifact-to-insert lag.
    if new is None or new["created_at"] != scored.measured_at \
            or abs(new["created_at"] - old["created_at"]) > MAX_CLOCK_GAP:
        print(f"\nWROTE row {new_id} but its created_at is not the artifact's "
              f"measurement time. Deleting NOTHING. Inspect both rows by hand.",
              file=sys.stderr)
        return 1
    for name in FAMILY:
        if not _agrees(old[name], new[name]):
            print(f"\nWROTE row {new_id} but its {name} does not match the "
                  f"original. Deleting NOTHING. Inspect both rows by hand.",
                  file=sys.stderr)
            return 1

    with get_conn() as conn:
        conn.execute("DELETE FROM eval_runs WHERE id = %s", (args.row_id,))

    print(f"  re-filed      row {args.row_id} -> row {new_id}, "
          f"verified before the original was removed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
