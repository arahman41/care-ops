"""P4-3: run the intake load test end to end and commit a real artifact.

    python scripts/run_load_test.py
    python scripts/run_load_test.py --users 50 --spawn-rate 10 --duration 2m

Starts the intake service itself with FAKE_STRUCTURING=true (shared/config.py),
so this never spends against the Anthropic API and never measures the
vendor's response-time variance instead of the service's own concurrency
handling: see scripts/locustfile.py for why. Runs Locust headless against it,
parses the real CSV Locust writes (nothing here computes a percentile by
hand), deletes the rows this run wrote (tagged by
scripts/locustfile.py::EXTERNAL_REF), and writes a committed JSON artifact
under governance/eval_artifacts/, the same convention P1-4/P2-4/P3-2 use.

Requires a reachable Postgres with db/schema.sql applied (make db-init);
refuses to run rather than silently measuring nothing if the intake service
never comes up healthy.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from shared.config import settings                               # noqa: E402
from shared.db import get_conn                                   # noqa: E402
from scripts.locustfile import EXTERNAL_REF                      # noqa: E402

ARTIFACT_DIR = REPO_ROOT / "governance" / "eval_artifacts"
RAW_DIR = REPO_ROOT / "governance" / "eval_artifacts" / "load_test_raw"


def _db_reachable() -> bool:
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _wait_for_health(host: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{host}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise SystemExit(
        f"intake service never became healthy at {host} within {timeout}s")


def _start_intake(port: int) -> subprocess.Popen:
    env = {**os.environ, "FAKE_STRUCTURING": "true"}
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "services.intake.app:app",
         "--port", str(port), "--log-level", "warning"],
        cwd=REPO_ROOT, env=env)


def _run_locust(*, host: str, users: int, spawn_rate: int, duration: str,
                csv_prefix: Path) -> None:
    cmd = [
        sys.executable, "-m", "locust", "-f", "scripts/locustfile.py",
        "--headless", "-u", str(users), "-r", str(spawn_rate),
        "-t", duration, "--host", host, "--csv", str(csv_prefix),
        "--only-summary",
    ]
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"locust exited {result.returncode}")


def _parse_stats(csv_prefix: Path) -> dict:
    stats_file = csv_prefix.parent / f"{csv_prefix.name}_stats.csv"
    with open(stats_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    row = next((r for r in rows if r["Name"] == "Aggregated"), None)
    if row is None:
        raise SystemExit(
            f"{stats_file} has no 'Aggregated' row; Locust's CSV format may "
            f"have changed. Rows found: {[r['Name'] for r in rows]}")
    return {
        "request_count": int(row["Request Count"]),
        "failure_count": int(row["Failure Count"]),
        "requests_per_second": float(row["Requests/s"]),
        "p50_ms": float(row["50%"]),
        "p95_ms": float(row["95%"]),
        "p99_ms": float(row["99%"]),
        "max_ms": float(row["Max Response Time"]),
    }


def _cleanup() -> int:
    """Delete every row this run wrote, identified by EXTERNAL_REF, not by
    a timestamp window: a run that overlaps a developer's own manual testing
    must still only touch its own rows."""
    with get_conn() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM encounters WHERE external_ref = %s",
            (EXTERNAL_REF,)).fetchall()]
        if not ids:
            return 0
        conn.execute("DELETE FROM agent_decisions WHERE encounter_id = ANY(%s)",
                     (ids,))
        conn.execute("DELETE FROM notes WHERE encounter_id = ANY(%s)", (ids,))
        conn.execute("DELETE FROM encounters WHERE id = ANY(%s)", (ids,))
    return len(ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--spawn-rate", type=int, default=5)
    parser.add_argument("--duration", default="1m")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if not _db_reachable():
        raise SystemExit(
            "no reachable Postgres at "
            f"{settings.postgres_host}:{settings.postgres_port}. Run "
            "`docker compose up -d db` (or `make cluster-up`) first.")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    host = f"http://127.0.0.1:{args.port}"
    proc = _start_intake(args.port)
    try:
        _wait_for_health(host, timeout=30)

        started_at = datetime.now(timezone.utc)
        timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        csv_prefix = RAW_DIR / f"load_test_{timestamp}"

        _run_locust(host=host, users=args.users, spawn_rate=args.spawn_rate,
                   duration=args.duration, csv_prefix=csv_prefix)
        stats = _parse_stats(csv_prefix)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    deleted = _cleanup()

    artifact = {
        "measured_at": started_at.isoformat(),
        "config": {
            "users": args.users, "spawn_rate": args.spawn_rate,
            "duration": args.duration, "target": "POST /intake",
            "fake_structuring": True,
        },
        "stats": stats,
        "rows_cleaned_up": deleted,
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ARTIFACT_DIR / f"load_test_{timestamp}.json"
    out_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    print("\n==================== LOAD TEST (P4-3) ====================")
    print(f"  target          POST {host}/intake  (structuring stubbed)")
    print(f"  users           {args.users}  spawn-rate {args.spawn_rate}  "
         f"duration {args.duration}")
    print(f"  requests        {stats['request_count']}  "
         f"failures {stats['failure_count']}")
    print(f"  requests/sec    {stats['requests_per_second']:.2f}")
    print(f"  p50 / p95 / p99 {stats['p50_ms']:.0f}ms / "
         f"{stats['p95_ms']:.0f}ms / {stats['p99_ms']:.0f}ms")
    print(f"  artifact        {out_path.relative_to(REPO_ROOT)}")
    print(f"  rows cleaned up {deleted}")
    print()

    return 0 if stats["failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
