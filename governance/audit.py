"""P4-5: regenerate every published headline number, and refuse to agree with
one that cannot be reproduced.

The point of this module is not to print numbers. Printing numbers is a
report, and a report still leaves a human to eyeball it against the README.
This ASSERTS: each published value is declared here as a literal, the value is
independently regenerated from committed evidence, and the two are compared.
A mismatch is a failure, not a note.

Three properties make it an audit rather than a formality:

1. **No circularity.** A regenerated value never comes from the same place as
   the published literal. Published values are literals in CLAIMS below;
   regenerated values are recomputed from committed artifacts. If someone
   edits an artifact to match a wrong README, replay() and replay_coding()
   raise first, because both recompute from per-fact and per-note primitives
   and refuse to agree with a stored aggregate they cannot reproduce.

2. **No borrowed arithmetic.** Nothing here reimplements a metric. Structuring
   goes through governance.structuring_eval (per_encounter_counts and
   score_structuring), coding through governance.coding_benchmark and the
   shared BCa engine, drift through governance.drift. An audit with its own
   copy of the metric math would eventually disagree with the system it audits
   and the disagreement would be the audit's fault.

3. **No silent pass for what it could not check.** A claim that needs live
   infrastructure, or that is a recorded observation rather than a
   computation, is reported as exactly that. The summary counts verified,
   consistent, skipped and failed separately, so "nothing is unbacked" is a
   statement a reader can check rather than take on faith.

Backing tiers, strongest first:

  RECOMPUTED   derived from primitives in a committed artifact (per-fact
               verdicts, per-note tallies, token counts). Catches a corrupted
               stored aggregate as well as a stale README.
  ARTIFACT     read from a committed artifact's stored field. Catches a stale
               README; cannot catch a corrupted artifact.
  ENVIRONMENT  needs live infrastructure (a cluster, a database). Checked when
               available, SKIPPED and said out loud when not.
  OBSERVED     a recorded live-run or human-audit observation, not re-derivable
               on demand. Checked for cross-document consistency instead: the
               value must appear both in the README and in the document that
               records it.

Deliberately NOT presence-checked against docs/ROADMAP.md. The roadmap is the
historical record and knowingly preserves superseded numbers (P2-4's $6.01
cost under the pricing table of the day, left as reported with the correction
beside it). A "must appear" check would be wrong there, and a "must not
appear" check would fail on exactly those deliberate records.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = REPO_ROOT / "governance" / "eval_artifacts"
README = REPO_ROOT / "README.md"

# Pinned by exact filename, never "the newest match". A glob would silently
# repoint a published claim at a different run the moment one is added, which
# is the P3-2 smoke-artifact failure in a new costume.
ACI_W1 = ARTIFACT_DIR / "structuring_aci-bench-heldout-v1_20260714T032403Z.json"
ACI_W2 = ARTIFACT_DIR / "structuring_aci-bench-heldout-v1_20260831T205449Z.json"
PRIMOCK = ARTIFACT_DIR / "structuring_primock57-heldout-v1_20260714T093650Z.json"
CODING = ARTIFACT_DIR / "coding_20260807T214249Z.json"
LOAD_TEST = ARTIFACT_DIR / "load_test_20260903T173359Z.json"

# scripts/run_coding_benchmark.py's pinned draw. Hardcoded rather than
# imported for the same reason tests/test_bootstrap_regression.py hardcodes
# it: this pins what the committed run USED, so editing the constant shows up
# as a failure instead of being followed silently.
CODING_SEED, CODING_REPLICATES = 20260722, 10000


class Backing(Enum):
    RECOMPUTED = "recomputed"
    ARTIFACT = "artifact"
    ENVIRONMENT = "environment"
    OBSERVED = "observed"


class Status(Enum):
    VERIFIED = "verified"        # regenerated and matched
    CONSISTENT = "consistent"    # OBSERVED: found in the README and its record
    SKIPPED = "skipped"          # ENVIRONMENT: infrastructure not available
    MISMATCH = "mismatch"        # regenerated and did NOT match
    UNBACKED = "unbacked"        # evidence missing, so nothing backs the claim


FAILING = (Status.MISMATCH, Status.UNBACKED)


@dataclass(frozen=True)
class Claim:
    """One published number, exactly as a reader sees it."""

    key: str
    published: str
    backing: Backing
    where: str                      # what backs it, or how to reproduce it
    record: str | None = None       # OBSERVED only: the doc that records it
    in_readme: bool = True


@dataclass(frozen=True)
class Finding:
    claim: Claim
    status: Status
    regenerated: str | None
    detail: str = ""


# ---------------------------------------------------------------------------
# The claims. This tuple is the point of the file: one reviewable list of every
# number this project publishes, and what backs each one.
# ---------------------------------------------------------------------------

CLAIMS: tuple[Claim, ...] = (
    # ---- note structuring, ACI-Bench held-out (n=120) ----
    Claim("aci_f1", "0.869", Backing.RECOMPUTED,
          "replay of ACI window 1, recomputed from per-fact verdicts"),
    Claim("aci_recall", "0.786", Backing.RECOMPUTED, "replay of ACI window 1"),
    Claim("aci_precision", "0.971", Backing.RECOMPUTED, "replay of ACI window 1"),
    Claim("aci_placement", "0.880", Backing.RECOMPUTED, "replay of ACI window 1"),
    Claim("aci_hallucination", "0.029", Backing.RECOMPUTED,
          "replay of ACI window 1"),
    Claim("aci_n", "120", Backing.ARTIFACT, "ACI window 1 n_examples"),
    Claim("aci_capture_rate", "0.893", Backing.RECOMPUTED,
          "captured / ref_facts over ACI window 1"),
    Claim("aci_capture_fraction", "5850 / 6550", Backing.RECOMPUTED,
          "captured and ref_facts over ACI window 1"),
    Claim("aci_fused_notes", "51", Backing.ARTIFACT,
          "ACI window 1 fused_ap_notes"),
    Claim("aci_strict_n", "69", Backing.RECOMPUTED,
          "count of non-fused examples in ACI window 1"),
    Claim("aci_strict_f1", "0.869", Backing.RECOMPUTED,
          "score_structuring over the non-fused subset of ACI window 1"),
    Claim("aci_strict_placement", "0.879", Backing.RECOMPUTED,
          "score_structuring over the non-fused subset of ACI window 1"),

    # ---- note structuring from audio, PriMock57 held-out (n=7) ----
    Claim("primock_f1", "0.899", Backing.RECOMPUTED, "replay of PriMock57"),
    Claim("primock_precision", "0.967", Backing.RECOMPUTED, "replay of PriMock57"),
    Claim("primock_hallucination", "0.033", Backing.RECOMPUTED,
          "replay of PriMock57"),
    Claim("primock_placement_null", "not scored", Backing.RECOMPUTED,
          "replay forces accuracy back to None when placement_scored is false"),
    Claim("primock_n", "7", Backing.ARTIFACT, "PriMock57 n_examples"),
    Claim("primock_capture_rate", "0.840", Backing.RECOMPUTED,
          "captured / ref_facts over PriMock57"),
    Claim("primock_capture_fraction", "215 / 256", Backing.RECOMPUTED,
          "captured and ref_facts over PriMock57"),
    Claim("primock_highlights_recall", "0.897", Backing.ARTIFACT,
          "PriMock57 highlights_recall (per-highlight verdicts are not in the "
          "redacted artifact, so this is a stored aggregate)"),
    Claim("primock_highlights_fraction", "26 / 29", Backing.ARTIFACT,
          "PriMock57 highlights_found and highlights_total"),

    # ---- coding routing benchmark (P2-4) ----
    Claim("coding_n", "113", Backing.RECOMPUTED,
          "analysis-set pairs rebuilt from the committed per-note tallies"),
    Claim("coding_arm_a_verified", "96.65", Backing.RECOMPUTED,
          "replay_coding, recomputed from per-note tallies"),
    Claim("coding_arm_b_verified", "97.35", Backing.RECOMPUTED,
          "replay_coding, recomputed from per-note tallies"),
    Claim("coding_arm_a_cost", "$4.01", Backing.RECOMPUTED,
          "arm A token counts priced at the CURRENT governance/pricing.json. "
          "This is the number whose earlier $6.01 was wrong; recomputing it "
          "here is what stops that recurring"),
    Claim("coding_arm_b_cost", "$3.16", Backing.RECOMPUTED,
          "arm B token counts priced at the current governance/pricing.json"),
    Claim("coding_cost_margin", "$0.84", Backing.RECOMPUTED,
          "arm A cost minus arm B cost, subtracted BEFORE rounding"),
    Claim("coding_delta_points", "0.70", Backing.RECOMPUTED,
          "paired BCa bootstrap re-run over the rebuilt pairs"),
    Claim("coding_delta_ci", "[-0.73, 2.22]", Backing.RECOMPUTED,
          "paired BCa bootstrap re-run over the rebuilt pairs"),

    # ---- drift detection (P3-3) ----
    Claim("drift_verdict", "NOT_ATTRIBUTABLE", Backing.RECOMPUTED,
          "compare_structuring_windows over the two committed ACI windows"),
    Claim("drift_delta", "+0.0052", Backing.RECOMPUTED,
          "compare_structuring_windows over the two committed ACI windows"),
    Claim("drift_ci", "[+0.000303, +0.010885]", Backing.RECOMPUTED,
          "compare_structuring_windows; published in docs/ROADMAP.md P3-3",
          in_readme=False),
    Claim("drift_mde", "0.005291", Backing.RECOMPUTED,
          "compare_structuring_windows; published in docs/ROADMAP.md P3-3",
          in_readme=False),
    # The pool the sensitivity sweep draws from: tests/test_drift.py::degrade
    # flips `found` off on facts that are currently found, so the flippable
    # population is window 2's captured count, not its reference-fact count
    # (6,553). Publishing "out of 5,875" without this check would leave the
    # denominator of the sensitivity claim resting on nothing.
    Claim("drift_injection_pool", "5,875", Backing.RECOMPUTED,
          "captured facts in ACI window 2, recomputed from its verdicts"),

    # ---- load test (P4-3) ----
    # A fresh measurement, so it is pinned to the committed artifact of the run
    # the README actually cites. Re-running produces a new number and SHOULD
    # fail this until the README is updated to cite the new run.
    Claim("load_requests", "851", Backing.ARTIFACT, f"{LOAD_TEST.name}"),
    Claim("load_failures", "0", Backing.ARTIFACT, f"{LOAD_TEST.name}"),
    Claim("load_rps", "14.25", Backing.ARTIFACT, f"{LOAD_TEST.name}"),
    Claim("load_p50", "110ms", Backing.ARTIFACT, f"{LOAD_TEST.name}"),
    Claim("load_p95", "240ms", Backing.ARTIFACT, f"{LOAD_TEST.name}"),
    Claim("load_p99", "370ms", Backing.ARTIFACT, f"{LOAD_TEST.name}"),

    # ---- environment ----
    Claim("k8s_pods", "6/6", Backing.ENVIRONMENT,
          "kubectl get pods -n care-ops"),
    Claim("suite_passed", "447", Backing.ENVIRONMENT,
          "pytest, with a live Postgres; --with-suite"),
    Claim("suite_coverage", "96%", Backing.ENVIRONMENT,
          "pytest --cov, with a live Postgres; --with-suite"),

    # ---- observed, not re-derivable ----
    Claim("concurrency_sum_ms", "9,652 ms", Backing.OBSERVED,
          "one live in-cluster run recorded in P2-6",
          record="docs/ROADMAP.md"),
    Claim("concurrency_wall_ms", "5,661 ms", Backing.OBSERVED,
          "the same live run: wall clock under the sum, so sequential "
          "execution was arithmetically impossible",
          record="docs/ROADMAP.md"),
    Claim("judge_audit", "29 / 30", Backing.OBSERVED,
          "hand audit of 30 sampled judge verdicts; the SAMPLE is reproducible "
          "from its seed, the human verdicts are not",
          record="docs/HELD-OUT-POLICY.md"),
    Claim("judge_audit_pct", "96.7", Backing.OBSERVED,
          "the same hand audit", record="docs/HELD-OUT-POLICY.md"),
    Claim("drift_sensitivity_fraction", "0.05%", Backing.OBSERVED,
          "the smallest injected fraction the detector flags. The OUTCOME is "
          "asserted by tests/test_drift.py::test_sensitivity_sweep on every "
          "push, which is stronger backing than this cross-check; what is "
          "checked here is that the README and the roadmap state the same "
          "fraction",
          record="docs/ROADMAP.md"),
)


# ---------------------------------------------------------------------------
# Regeneration
# ---------------------------------------------------------------------------

def _appears_in(text: str, value: str) -> bool:
    """Whole-token search, so 0.84 does not match inside 0.840."""
    pattern = re.escape(value)
    if value[0].isdigit():
        pattern = r"(?<![0-9.])" + pattern
    if value[-1].isdigit():
        pattern = pattern + r"(?![0-9])"
    return re.search(pattern, text) is not None


def _structuring(path: Path) -> dict[str, str]:
    """Every structuring claim for one window, recomputed from its verdicts."""
    from governance.evaluate import score_structuring, StructuringCounts
    from governance.structuring_eval import per_encounter_counts, replay

    out = replay(path)                       # raises if stored != recomputed
    metrics, counts, payload = out["metrics"], out["counts"], out["payload"]
    prefix = "aci" if "aci-bench" in path.name else "primock"

    values = {
        f"{prefix}_f1": f"{metrics['f1']:.3f}",
        f"{prefix}_precision": f"{metrics['precision']:.3f}",
        f"{prefix}_hallucination": f"{metrics['hallucination_rate']:.3f}",
        f"{prefix}_n": str(payload["n_examples"]),
        f"{prefix}_capture_rate": f"{counts.captured / counts.ref_facts:.3f}",
        f"{prefix}_capture_fraction": f"{counts.captured} / {counts.ref_facts}",
    }

    if metrics["accuracy"] is None:
        # PriMock57. replay() forces this back to None because placement is
        # not scorable against an unsectioned note; the README says exactly
        # that, so the regenerated value is the words, not a number.
        values[f"{prefix}_placement_null"] = "not scored"
    else:
        values[f"{prefix}_recall"] = f"{metrics['recall']:.3f}"
        values[f"{prefix}_placement"] = f"{metrics['accuracy']:.3f}"
        values[f"{prefix}_fused_notes"] = str(payload["fused_ap_notes"])

        # The strict subset, recomputed rather than read: sum the per-encounter
        # counts of the notes whose reference separates A from P, then score
        # them with the same score_structuring the headline uses.
        per_encounter = per_encounter_counts(payload)
        unfused = [ex["encounter_id"] for ex in payload["examples"]
                   if not ex["fused"]]
        strict = StructuringCounts(0, 0, 0, 0, 0)
        for encounter_id in unfused:
            strict = strict + per_encounter[encounter_id]
        strict_metrics = score_structuring(strict)
        values[f"{prefix}_strict_n"] = str(len(unfused))
        values[f"{prefix}_strict_f1"] = f"{strict_metrics['f1']:.3f}"
        values[f"{prefix}_strict_placement"] = f"{strict_metrics['accuracy']:.3f}"

    if payload.get("highlights_total"):
        values[f"{prefix}_highlights_recall"] = f"{payload['highlights_recall']:.3f}"
        values[f"{prefix}_highlights_fraction"] = (
            f"{payload['highlights_found']} / {payload['highlights_total']}")
    return values


def _coding() -> dict[str, str]:
    """Coding rates recomputed from per-note tallies, cost repriced from
    token counts at the CURRENT price table, and the interval re-bootstrapped."""
    from governance.coding_benchmark import replay_coding
    from governance.coding_bootstrap import paired_bootstrap_bca, pairs_from_artifact
    from governance.pricing import cost_usd, load_price_table

    arms = replay_coding(CODING)             # raises if stored != recomputed
    payload = json.loads(CODING.read_text(encoding="utf-8"))

    values = {
        "coding_arm_a_verified": f"{arms['A']['verified_rate']:.2f}",
        "coding_arm_b_verified": f"{arms['B']['verified_rate']:.2f}",
    }

    table = load_price_table()
    if table is not None:
        costs = {}
        for arm, model in (("A", "claude-sonnet-5"), ("B", "claude-opus-4-8")):
            costs[arm] = cost_usd(table, model,
                                  arms[arm]["input_tokens"],
                                  arms[arm]["output_tokens"])
        values["coding_arm_a_cost"] = f"${costs['A']:.2f}"
        values["coding_arm_b_cost"] = f"${costs['B']:.2f}"
        # Subtract THEN round. Rounding first gives $0.85, which is how
        # governance/pricing.json's own note came to disagree with every other
        # statement of this margin by a cent.
        values["coding_cost_margin"] = f"${costs['A'] - costs['B']:.2f}"

    pairs = pairs_from_artifact(payload)
    boot = paired_bootstrap_bca(pairs, seed=CODING_SEED,
                                replicates=CODING_REPLICATES)
    values["coding_n"] = str(len(pairs))
    values["coding_delta_points"] = f"{boot.d:.2f}"
    values["coding_delta_ci"] = f"[{boot.ci[0]:.2f}, {boot.ci[1]:.2f}]"
    return values


def _drift() -> dict[str, str]:
    """The real window pair, re-run through the paired bootstrap."""
    from governance.drift import compare_structuring_windows
    from governance.evaluate import StructuringCounts
    from governance.structuring_eval import per_encounter_counts

    reference = json.loads(ACI_W1.read_text(encoding="utf-8"))
    current = json.loads(ACI_W2.read_text(encoding="utf-8"))
    result = compare_structuring_windows(reference, current)

    window2 = sum(per_encounter_counts(current).values(),
                  StructuringCounts(0, 0, 0, 0, 0))
    return {
        "drift_verdict": result.verdict.value.upper(),
        "drift_delta": f"{result.delta:+.4f}",
        "drift_ci": f"[{result.ci[0]:+.6f}, {result.ci[1]:+.6f}]",
        "drift_mde": f"{result.mde:.6f}",
        "drift_injection_pool": f"{window2.captured:,}",
    }


def _load_test() -> dict[str, str]:
    stats = json.loads(LOAD_TEST.read_text(encoding="utf-8"))["stats"]
    return {
        "load_requests": str(stats["request_count"]),
        "load_failures": str(stats["failure_count"]),
        "load_rps": f"{stats['requests_per_second']:.2f}",
        "load_p50": f"{stats['p50_ms']:.0f}ms",
        "load_p95": f"{stats['p95_ms']:.0f}ms",
        "load_p99": f"{stats['p99_ms']:.0f}ms",
    }


def _kubernetes() -> dict[str, str]:
    """Ready pods over total pods in the care-ops namespace, or nothing."""
    try:
        proc = subprocess.run(
            ["kubectl", "get", "pods", "-n", "care-ops", "-o", "json"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}

    pods = json.loads(proc.stdout).get("items", [])
    if not pods:
        return {}
    ready = sum(
        1 for p in pods
        if p.get("status", {}).get("phase") == "Running"
        and all(c.get("ready") for c in p["status"].get("containerStatuses", []))
        and p["status"].get("containerStatuses"))
    return {"k8s_pods": f"{ready}/{len(pods)}"}


def _parse_suite_output(text: str) -> dict[str, str]:
    """Read the pass count and coverage off a pytest run.

    A run with ANY skipped test is an incomplete environment (almost always a
    missing Postgres, which skips every needs_db test). Its lower count is not
    evidence against the published one, so this returns nothing rather than a
    wrong answer: reporting 406 as a contradiction of a published 430 would be
    the audit manufacturing a finding out of its own missing dependency.
    """
    if re.search(r"\d+ skipped", text):
        return {}

    values = {}
    passed = re.search(r"(\d+) passed", text)
    if passed:
        values["suite_passed"] = passed.group(1)
    coverage = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", text, re.MULTILINE)
    if coverage:
        values["suite_coverage"] = f"{coverage.group(1)}%"
    return values


def _suite() -> dict[str, str]:
    """Run the real suite with coverage and read the numbers off it."""
    # sys.executable, never a bare "python": on Windows that resolves to the
    # system install with none of the pinned deps, which is the same bug the
    # Makefile's PY variable exists to prevent (recorded in ROADMAP P2-4).
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--cov=shared", "--cov=services",
         "--cov=governance", "--cov-report=term", "-q"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800)
    return _parse_suite_output(proc.stdout + proc.stderr)


def regenerate(*, with_suite: bool = False) -> tuple[dict[str, str], list[str]]:
    """Every regenerable value, plus the reasons any group could not be done."""
    values: dict[str, str] = {}
    problems: list[str] = []

    groups = [
        ("ACI window 1", lambda: _structuring(ACI_W1)),
        ("PriMock57", lambda: _structuring(PRIMOCK)),
        ("coding benchmark", _coding),
        ("drift", _drift),
        ("load test", _load_test),
        ("kubernetes", _kubernetes),
    ]
    if with_suite:
        groups.append(("test suite", _suite))

    for name, fn in groups:
        try:
            values.update(fn())
        except FileNotFoundError as exc:
            problems.append(f"{name}: missing evidence ({exc})")
        except Exception as exc:                       # noqa: BLE001
            # A regenerator that throws is a finding, not a crash: the audit
            # still has to report on every other claim.
            problems.append(f"{name}: {type(exc).__name__}: {exc}")

    return values, problems


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

def audit(*, with_suite: bool = False) -> tuple[list[Finding], list[str]]:
    values, problems = regenerate(with_suite=with_suite)
    readme = README.read_text(encoding="utf-8") if README.exists() else ""
    findings = []

    for claim in CLAIMS:
        if claim.in_readme and not _appears_in(readme, claim.published):
            findings.append(Finding(
                claim, Status.MISMATCH, values.get(claim.key),
                f"published value {claim.published!r} does not appear in "
                f"README.md, so the manifest and the README disagree"))
            continue

        if claim.backing is Backing.OBSERVED:
            findings.append(_observed(claim))
            continue

        regenerated = values.get(claim.key)
        if regenerated is None:
            status = (Status.SKIPPED if claim.backing is Backing.ENVIRONMENT
                      else Status.UNBACKED)
            detail = ("infrastructure not available, value not re-measured "
                      "here" if status is Status.SKIPPED else
                      "nothing regenerated a value for this claim")
            findings.append(Finding(claim, status, None, detail))
            continue

        if regenerated == claim.published:
            findings.append(Finding(claim, Status.VERIFIED, regenerated))
        else:
            findings.append(Finding(
                claim, Status.MISMATCH, regenerated,
                f"published {claim.published!r}, regenerated {regenerated!r}"))

    return findings, problems


def _observed(claim: Claim) -> Finding:
    """An observation cannot be recomputed, so check it says the same thing in
    both places it is written down."""
    record = REPO_ROOT / claim.record
    if not record.exists():
        return Finding(claim, Status.UNBACKED, None,
                       f"record document {claim.record} is missing")
    if not _appears_in(record.read_text(encoding="utf-8"), claim.published):
        return Finding(claim, Status.MISMATCH, None,
                       f"{claim.published!r} is in the README but not in "
                       f"{claim.record}, which is supposed to record it")
    return Finding(claim, Status.CONSISTENT, claim.published,
                   f"recorded in {claim.record}; not re-derivable on demand")


def summarize(findings: list[Finding]) -> dict[Status, int]:
    counts = {status: 0 for status in Status}
    for finding in findings:
        counts[finding.status] += 1
    return counts


def failed(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.status in FAILING]
