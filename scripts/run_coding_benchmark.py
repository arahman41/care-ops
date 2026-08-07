"""P2-4 coding routing benchmark CLI.

    python scripts/run_coding_benchmark.py --pilot
    python scripts/run_coding_benchmark.py            # full run (human-gated; spends)
    python scripts/run_coding_benchmark.py --replay governance/eval_artifacts/<f>.json

Verifies the held-out split before any API call. Refuses to name a cost winner
if governance/pricing.json is absent, leaving ROUTING["coding"] unchanged.
Never writes to agent_decisions (the benchmark does not call agent.run()).

Nothing here is a coding-accuracy number, and no result is attributable to a
model. The unit under test is a (model, effort) CONFIGURATION.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from governance.coding_benchmark import (                     # noqa: E402
    ARTIFACT_DIR,
    aggregate_tallies,
    assemble_run,
    build_committed_artifact,
    build_roster,
    build_soap_from_reference,
    plan_is_empty,
    replay_coding,
    run_arm_on_note,
)
from governance.coding_bootstrap import paired_bootstrap_bca  # noqa: E402
from governance.coding_decision import (                      # noqa: E402
    ArmGuardStats, Branch, decide,
)
from governance.coding_metrics import dedupe_note             # noqa: E402
from governance.coding_pilot import (                         # noqa: E402
    equivalence_attainable, load_aci_train, pearson_rho, pilot_v_check,
    pin_pilot_ids,
)
from governance.evaluate import record_coding_run             # noqa: E402
from governance.heldout import (                              # noqa: E402
    ACI_DATASET_REF, SplitDriftError, load_aci_heldout, verify_split,
)
from governance.llm_cache import Cache                        # noqa: E402
from governance.pricing import cost_usd, load_price_table     # noqa: E402
from governance.structuring_eval import CACHE_DIR, locked_digest  # noqa: E402
from shared import vocab                                      # noqa: E402

# The two configurations under test. Named arms, never "the Sonnet result".
ARM_A = ("claude-sonnet-5", "xhigh")
ARM_B = ("claude-opus-4-8", "high")

# Pinned and printed, so both draws are recorded and reproducible (spec §4, §8).
PILOT_SEED = 20260722
BOOTSTRAP_SEED = 20260722
BOOTSTRAP_REPLICATES = 10000

AGENT_NAME = "coding"
VOCAB_FLOOR_VERSION = "none"     # no FY2025 floor pin unless Task 8.1 clears it


def _run_arms(notes, cache, workers, on_done=None):
    """Run both arms over {eid: SoapNote}. Returns (results_a, results_b)."""
    def _one(item):
        eid, soap = item
        ra = run_arm_on_note(soap, model=ARM_A[0], effort=ARM_A[1], cache=cache)
        rb = run_arm_on_note(soap, model=ARM_B[0], effort=ARM_B[1], cache=cache)
        if on_done:
            on_done()
        return eid, ra, rb

    results_a, results_b = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for eid, ra, rb in pool.map(_one, notes.items()):
            results_a[eid] = ra
            results_b[eid] = rb
    return results_a, results_b


def _arm_guard_stats(agg: dict) -> ArmGuardStats:
    return ArmGuardStats(unchecked_share=agg["unchecked_share"] or 0.0,
                         codes_per_note=agg["codes_per_note"],
                         floor_lower=agg["floor_lower"],
                         floor_upper=agg["floor_upper"])


def _roster_rows(results, arm_label, ids):
    rows = []
    for eid in ids:
        for code in dedupe_note(results[eid].output):
            rows.append({"encounter_id": eid, "arm": arm_label,
                         "systems_seen": list(code.systems_seen),
                         "code": code.key,
                         "model_description": " | ".join(code.descriptions),
                         "auto_cause": None, "adjudication": ""})
    return rows


# ---------------------------------------------------------------- pilot

def _pilot(args) -> int:
    ids = pin_pilot_ids(n=args.pilot_n, seed=PILOT_SEED)
    by_id = {e.encounter_id: e for e in load_aci_train()}

    print(f"\nPILOT: {len(ids)} ACI TRAIN notes, seed {PILOT_SEED}.")
    print(f"pinned ids: {', '.join(ids)}")
    print("Train split only. The held-out set is never touched here, and no "
          "held-out verified rate is reported.\n")

    notes = {eid: build_soap_from_reference(by_id[eid].reference_note)
             for eid in ids}
    cache = Cache(CACHE_DIR)
    results_a, results_b = _run_arms(notes, cache, args.workers)

    truncations = sum(1 for r in list(results_a.values()) + list(results_b.values())
                      if r.failure and "truncat" in r.failure.lower())
    failures = [(eid, r.failure)
                for eid, r in list(results_a.items()) + list(results_b.items())
                if r.failure]

    run = assemble_run(results_a, results_b,
                       {eid: plan_is_empty(notes[eid]) for eid in ids})
    if not run.analysis.ids:
        print("Every pilot note failed in at least one arm. Nothing to report.")
        for eid, why in failures:
            print(f"  {eid}: {why}")
        return 1

    agg_a = aggregate_tallies(list(run.arm_tallies["A"].values()))
    agg_b = aggregate_tallies(list(run.arm_tallies["B"].values()))

    # Per-note not-found rates drive rho and the per-arm SE.
    def _per_note_nf(arm):
        out = []
        for eid in run.analysis.ids:
            t = run.arm_tallies[arm][eid]
            ck = t.verified + t.not_found
            out.append(100.0 * t.not_found / ck if ck else 0.0)
        return out

    nf_a, nf_b = _per_note_nf("A"), _per_note_nf("B")
    rho = pearson_rho(nf_a, nf_b)
    n = len(run.analysis.ids)
    se_arm = (statistics.stdev(nf_a) / (n ** 0.5)) if n > 1 else 0.0
    attainable = equivalence_attainable(se_arm_points=se_arm, rho=rho,
                                        n_pilot=n, n_target=120)
    v = agg_a["verified_rate"]
    v_check = pilot_v_check(v) if v is not None else {"escalate": False,
                                                      "v_pilot": None}

    print("=============== PILOT DIAGNOSTICS (train split) ===============")
    print(f"analysis notes            {n} of {len(ids)}")
    print(f"truncations               {truncations}")
    for eid, why in failures:
        print(f"  failure {eid}: {why}")
    print()
    for label, arm, agg in (("A", ARM_A, agg_a), ("B", ARM_B, agg_b)):
        print(f"arm {label}  {arm[0]} at {arm[1]}")
        print(f"  codes/note              {agg['codes_per_note']:.2f}")
        print(f"  unchecked share         {agg['unchecked_share']}")
        print(f"  floor band (points)     [{agg['floor_lower']:.2f}, "
              f"{agg['floor_upper']:.2f}]")
        print(f"  tokens in/out           {agg['input_tokens']}/{agg['output_tokens']}")
        print(f"  latency p50/p95 (ms)    {agg['latency_p50']}/{agg['latency_p95']}")
    print()
    print(f"between-arm rho           {rho}")
    print(f"per-arm SE (points)       {se_arm:.3f}")
    print(f"design effect proxy       {(2 * (1 - (rho or 0.0))):.3f}  "
          f"(2(1-rho); 0 means perfectly paired)")
    print(f"equivalence attainable    {attainable}  at n=120 against "
          f"delta/1.96 = {1.5 / 1.96:.3f} points")
    print(f"pilot v (arm A verified)  {v_check['v_pilot']}   "
          f"escalate={v_check['escalate']}")
    print()
    print("Guard preview (would these trip at these values?):")
    ga, gb = _arm_guard_stats(agg_a), _arm_guard_stats(agg_b)
    print(f"  unchecked gap           {abs(ga.unchecked_share - gb.unchecked_share):.2f}"
          f"  (guard fires above 1.6)")
    mean_cpn = (ga.codes_per_note + gb.codes_per_note) / 2.0
    vol = (abs(ga.codes_per_note - gb.codes_per_note) / mean_cpn) if mean_cpn else 0.0
    print(f"  volume divergence       {vol:.3f}  (guard fires above 0.25)")
    print(f"  max floor gap           "
          f"{max(ga.floor_upper - gb.floor_lower, gb.floor_upper - ga.floor_lower, 0.0):.2f}"
          f"  (guard fires above |d|)")
    print("==============================================================")
    print("\nThis is the TRAIN split. It sizes the run; it decides nothing.")
    print("The draw is pinned and must not be redrawn for a nicer answer.\n")
    return 0


# ----------------------------------------------------------------- full run

def _full(args) -> int:
    examples = load_aci_heldout()
    print(f"\nFULL RUN: {len(examples)} held-out notes x 2 arms = "
          f"{2 * len(examples)} calls. Cached calls are free.")
    print(f"arm A  {ARM_A[0]} at {ARM_A[1]}")
    print(f"arm B  {ARM_B[0]} at {ARM_B[1]}")
    print(f"bootstrap seed {BOOTSTRAP_SEED}, {BOOTSTRAP_REPLICATES} replicates\n")

    notes = {e.encounter_id: build_soap_from_reference(e.reference_note)
             for e in examples}
    strata = {eid: plan_is_empty(soap) for eid, soap in notes.items()}
    print(f"strata: {sum(strata.values())} empty-plan, "
          f"{len(strata) - sum(strata.values())} non-empty\n")

    done = [0]

    def progress():
        done[0] += 1
        print(f"\r  {done[0]}/{len(notes)}", end="", flush=True)

    cache = Cache(CACHE_DIR)
    results_a, results_b = _run_arms(notes, cache, args.workers, progress)
    print()

    run = assemble_run(results_a, results_b, strata)
    lengths = {e.encounter_id: len(e.reference_note) for e in examples}
    from governance.coding_benchmark import attrition_length_summary
    attrition = attrition_length_summary(
        [lengths[i] for i in run.analysis.dropped_ids],
        [lengths[i] for i in run.analysis.ids])

    print(f"analysis set: {len(run.analysis.ids)} of {len(notes)} "
          f"(dropped {len(run.analysis.dropped_ids)})")
    if run.analysis.dropped_ids:
        print(f"attrition by note length: {attrition}")

    if run.analysis.is_void:
        print("\nVOID: the intersection fell below 108 of 120. No comparison is "
              "computed and ROUTING is unchanged.")
        return 1

    agg_a = aggregate_tallies(list(run.arm_tallies["A"].values()))
    agg_b = aggregate_tallies(list(run.arm_tallies["B"].values()))
    boot = paired_bootstrap_bca(run.note_pairs, seed=BOOTSTRAP_SEED,
                                replicates=BOOTSTRAP_REPLICATES)

    std_better = "A" if agg_a["not_found_rate"] < agg_b["not_found_rate"] else "B"
    pes_better = ("A" if agg_a["pessimistic_verified_rate"]
                  > agg_b["pessimistic_verified_rate"] else "B")

    decision = decide(d=boot.d, ci=boot.ci,
                      arm_a=_arm_guard_stats(agg_a),
                      arm_b=_arm_guard_stats(agg_b),
                      n_analysis=len(run.analysis.ids),
                      nf_rate_a=agg_a["not_found_rate"],
                      nf_rate_b=agg_b["not_found_rate"],
                      standard_better_arm=std_better,
                      pessimistic_better_arm=pes_better)

    # Cost, only where a price table exists. Its absence is terminal (spec §8).
    table = load_price_table()
    cost = {"price_table_present": table is not None}
    if table is not None:
        try:
            cost["A_usd"] = cost_usd(table, ARM_A[0], agg_a["input_tokens"],
                                     agg_a["output_tokens"])
            cost["B_usd"] = cost_usd(table, ARM_B[0], agg_b["input_tokens"],
                                     agg_b["output_tokens"])
            cost["cheaper_arm"] = "A" if cost["A_usd"] < cost["B_usd"] else "B"
            cost["source"] = table.source
            cost["retrieved"] = table.retrieved
        except KeyError as exc:
            print(f"\nprice table is missing a model: {exc}. No cost winner.")
            cost = {"price_table_present": False, "missing_model": str(exc)}

    winner = None
    if decision.branch is Branch.DIFFERENCE:
        winner = decision.winner_arm
    elif decision.route_on == "cost" and cost.get("cheaper_arm"):
        winner = cost["cheaper_arm"]

    comparison = {
        "delta_points": boot.d,
        "delta_ci95": list(boot.ci),
        "branch_fired": decision.branch.value,
        "guards_tripped": decision.guards_tripped,
        "route_on": decision.route_on,
        "winner_arm": winner,
        "winner_configuration": (
            {"A": ARM_A, "B": ARM_B}[winner] if winner else None),
        "framing_disagreement": decision.framing_disagreement,
        "standard_better_arm": std_better,
        "pessimistic_better_arm": pes_better,
        "reason": decision.reason,
        "bootstrap": {"seed": boot.seed, "replicates": boot.replicates,
                      "retained": boot.retained, "dropped": boot.dropped,
                      "acceleration": boot.acceleration,
                      "acceleration_degenerate": boot.acceleration_degenerate},
        "cost": cost,
        "attrition_by_note_length": attrition,
        "note": ("A (model, effort) CONFIGURATION comparison of vocabulary "
                 "verified rates. NOT coding accuracy, and not attributable to "
                 "a model: neither held-out set carries gold billing codes."),
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = build_committed_artifact(
        arm_tallies=run.arm_tallies, agreement=run.agreement,
        strata=run.strata, comparison=comparison,
        run_meta={"vocab_version": vocab.VOCAB_VERSION,
                  "vocab_floor_version": VOCAB_FLOOR_VERSION,
                  "price_table_ref": (table.source if table else None),
                  "split_digest": locked_digest(),
                  "dataset_ref": ACI_DATASET_REF},
        arm_meta={"A": {"requested_model": ARM_A[0], "requested_effort": ARM_A[1],
                        "observed_model": results_a[run.analysis.ids[0]].observed_model},
                  "B": {"requested_model": ARM_B[0], "requested_effort": ARM_B[1],
                        "observed_model": results_b[run.analysis.ids[0]].observed_model}})

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    import json
    apath = ARTIFACT_DIR / f"coding_{stamp}.json"
    apath.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    rpath = ARTIFACT_DIR / f"coding_{stamp}.full.json"
    rpath.write_text(json.dumps(build_roster(
        _roster_rows(results_a, "A", run.analysis.ids)
        + _roster_rows(results_b, "B", run.analysis.ids)), indent=2),
        encoding="utf-8")

    _print_result(agg_a, agg_b, boot, decision, cost, winner, run)
    print(f"artifact  {apath.relative_to(REPO_ROOT)}")
    print(f"roster    {rpath.relative_to(REPO_ROOT)}   (gitignored, carries codes)")

    if args.no_db:
        print("\n--no-db: not writing eval_runs rows.")
        return 0

    for label, arm, agg in (("A", ARM_A, agg_a), ("B", ARM_B, agg_b)):
        row_id = record_coding_run(
            agent_name=AGENT_NAME, model=arm[0], model_effort=arm[1],
            window_label=args.window_label, dataset_ref=ACI_DATASET_REF,
            n_examples=len(run.analysis.ids),
            metrics={**agg, "arm": label, "comparison": comparison})
        print(f"eval_runs id = {row_id}  (arm {label})")
    return 0


def _print_result(agg_a, agg_b, boot, decision, cost, winner, run) -> None:
    print("\n========== CODING CONFIGURATION ROUTING BENCHMARK (P2-4) ==========")
    print(f"analysis notes  {len(run.analysis.ids)}")
    for label, arm, agg in (("A", ARM_A, agg_a), ("B", ARM_B, agg_b)):
        print(f"\narm {label}  {arm[0]} at {arm[1]}")
        print(f"  verified rate           {agg['verified_rate']:.2f} points")
        print(f"  not-found rate          {agg['not_found_rate']:.2f} points")
        print(f"  pessimistic verified    {agg['pessimistic_verified_rate']:.2f}")
        print(f"  unchecked share         {agg['unchecked_share']:.2f}")
        print(f"  floor band              [{agg['floor_lower']:.2f}, "
              f"{agg['floor_upper']:.2f}] points")
        print(f"  causes                  {agg['floor_cause_counts']}")
        print(f"  codes/note              {agg['codes_per_note']:.2f}")
    print(f"\npaired delta nf(A)-nf(B)  {boot.d:.2f} points")
    print(f"95% BCa CI                [{boot.ci[0]:.2f}, {boot.ci[1]:.2f}] points")
    print(f"branch                    {decision.branch.value}")
    print(f"guards tripped            {decision.guards_tripped or 'none'}")
    print(f"route on                  {decision.route_on}")
    if decision.framing_disagreement:
        print("FRAMING DISAGREEMENT: standard and pessimistic pick different "
              "arms. Report that as the finding.")
    if cost.get("price_table_present"):
        print(f"cost A/B (USD)            {cost['A_usd']:.4f} / {cost['B_usd']:.4f}")
    else:
        print("cost                      NO PRICE TABLE. No cost winner is "
              "named and ROUTING stays unchanged.")
    print(f"winning configuration     "
          f"{ {'A': ARM_A, 'B': ARM_B}[winner] if winner else 'NONE NAMED'}")
    print("\nThis compares (model, effort) CONFIGURATIONS on a vocabulary")
    print("verified rate. It is NOT coding accuracy and is not a model result.")
    print("===================================================================\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", action="store_true",
                        help="train-split sizing run; spends ~10 calls")
    parser.add_argument("--pilot-n", type=int, default=5)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-db", action="store_true",
                        help="skip the eval_runs writes")
    parser.add_argument("--window-label", default="v1")
    parser.add_argument("--replay", type=Path, default=None,
                        help="recompute every rate from a committed artifact")
    args = parser.parse_args()

    if args.replay:
        out = replay_coding(args.replay)
        print(f"\nReplayed {args.replay.name} with zero API calls.")
        for arm, agg in out.items():
            print(f"  arm {arm}  verified {agg['verified_rate']:.2f}  "
                  f"not-found {agg['not_found_rate']:.2f}  "
                  f"floor [{agg['floor_lower']:.2f}, {agg['floor_upper']:.2f}]  "
                  f"n={agg['n_notes']}")
        print("\nRecomputed from the per-note tallies and it matches the "
              "artifact.\n")
        return 0

    # The guard. Before anything is spent.
    try:
        verify_split()
    except SplitDriftError as exc:
        print(f"\nREFUSING TO RUN\n\n{exc}\n", file=sys.stderr)
        return 1

    return _pilot(args) if args.pilot else _full(args)


if __name__ == "__main__":
    raise SystemExit(main())
