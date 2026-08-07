"""P2-4 coding routing benchmark orchestration.

Builds byte-identical SoapNote inputs from ACI reference notes, runs both arms
through the shared parse path with per-arm (model, effort) overrides, caches a
serialized LLMResult per call, and forms the analysis set as the intersection of
notes both arms parsed. Touches the API and DB only at run time; every function
here is unit-tested with fakes.

Never calls agent.run(): that logs to agent_decisions with int encounter ids and
NOT NULL foreign keys, and ACI ids are strings like D2N068 (spec §7).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from governance.aci_sections import SOAP_BUCKETS, bucket_sections
from governance.coding_bootstrap import NotePair
from governance.coding_metrics import (
    DedupedCode, dedupe_note, floor_band, note_agreement, note_denominators,
)
from governance.llm_cache import Cache, cache_key
from governance.structuring_eval import hash_prompt
from services.agent_coding.agent import (
    CodingError, _MAX_TOKENS as CODING_MAX_TOKENS, _SYSTEM as CODING_SYSTEM,
    parse_and_enrich,
)
from shared import vocab
from shared.llm import LLMResult, TruncatedResponseError, call_detailed
from shared.schemas import SoapNote

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = REPO_ROOT / "governance" / "eval_artifacts"


def build_soap_from_reference(reference_note: str) -> SoapNote:
    """Concatenate the bodies of all sections sharing a `primary` bucket,
    joined with two newlines (spec §3). Fused ASSESSMENT AND PLAN reports as
    assessment via RefSection.primary, which is why a fused-only note has an
    empty plan."""
    by_bucket: dict[str, list[str]] = {b: [] for b in SOAP_BUCKETS}
    for section in bucket_sections(reference_note):
        by_bucket[section.primary].append(section.body)
    return SoapNote(**{b: "\n\n".join(by_bucket[b]) for b in SOAP_BUCKETS})


def plan_is_empty(soap: SoapNote) -> bool:
    """The stratification variable (spec §3): 27 empty, 93 non-empty."""
    return soap.plan == ""


def _cache_version_string(effort: str) -> str:
    """The prompt_version half of the cache key.

    Folds in effort, the system-prompt hash, and max_tokens. governance/facts.py
    and governance/judge.py pass a bare PROMPT_VERSION = "v1" literal, which is
    exactly how an edited prompt yields silent cache HITS that blend two prompt
    versions into one number. This is the strong form, as structuring_eval.py
    builds it.
    """
    return f"{effort}|{hash_prompt(CODING_SYSTEM)}|max{CODING_MAX_TOKENS}"


def _cache_key(model: str, effort: str, payload: str) -> str:
    """Built FROM _cache_version_string, so the key a response is stored under
    and the prompt_version recorded in the artifact cannot drift apart."""
    return cache_key("coding", model, _cache_version_string(effort), payload)


@dataclass(frozen=True)
class ArmNoteResult:
    output: object | None            # CodingOutput or None on failure
    observed_model: str | None
    tokens: tuple[int, int] | None   # (input, output)
    latency_ms: int | None           # None on a cache hit (wall-clock only)
    failure: str | None


def run_arm_on_note(soap: SoapNote, model: str, effort: str,
                    cache: Cache) -> ArmNoteResult:
    """Run one arm on one note, cached. A truncation or parse failure is typed,
    not raised (spec §8); an observed-model family mismatch IS raised (spec §6)."""
    payload = soap.model_dump_json()
    key = _cache_key(model, effort, payload)

    cached = cache.get(key)
    if cached is not None:
        result = LLMResult.from_json(cached)
        latency_ms = None
    else:
        started = time.perf_counter()
        try:
            result = call_detailed("coding", system=CODING_SYSTEM, user=payload,
                                   max_tokens=CODING_MAX_TOKENS,
                                   model=model, effort=effort)
        except TruncatedResponseError as exc:
            return ArmNoteResult(None, None, None, None, f"truncated: {exc}")
        latency_ms = int((time.perf_counter() - started) * 1000)
        cache.put(key, result.to_json())

    # Resolved-family match: ROUTING holds a bare alias, the API echoes a
    # snapshot id, so exact equality would hard-fail on note 1 (spec §6).
    if not result.model.startswith(model):
        raise ValueError(
            f"observed model {result.model!r} does not match requested "
            f"{model!r}; refusing to attribute this note's result to an arm")

    tokens = (result.input_tokens, result.output_tokens)
    try:
        output = parse_and_enrich(result.text)
    except CodingError as exc:
        return ArmNoteResult(None, result.model, tokens, latency_ms,
                             f"parse: {exc}")
    return ArmNoteResult(output, result.model, tokens, latency_ms, None)


INTERSECTION_MIN = 108        # 0.90 * 120 target notes (spec §2, §8)


@dataclass(frozen=True)
class AnalysisSet:
    ids: list[str]            # notes both arms parsed, sorted
    dropped_ids: list[str]    # notes at least one arm failed, sorted

    @property
    def is_void(self) -> bool:
        return len(self.ids) < INTERSECTION_MIN


def build_analysis_set(per_note_ok: dict[str, tuple[bool, bool]]) -> AnalysisSet:
    """Intersection of notes both arms parsed. Void is judged on the
    intersection size, never per arm (spec §8): two arms each failing 8% on
    disjoint notes would drop 16% without tripping any per-arm threshold."""
    keep, drop = [], []
    for eid, (a_ok, b_ok) in per_note_ok.items():
        (keep if a_ok and b_ok else drop).append(eid)
    return AnalysisSet(ids=sorted(keep), dropped_ids=sorted(drop))


def attrition_length_summary(dropped_lengths: list[int],
                             retained_lengths: list[int]) -> dict:
    """Reference-note length distribution of dropped vs retained notes, so the
    SHAPE of a non-random loss is visible, not merely bounded (spec §8)."""
    def _summ(xs: list[int]) -> dict:
        if not xs:
            return {"n": 0, "min": None, "median": None, "max": None, "mean": None}
        s = sorted(xs)
        mid = len(s) // 2
        median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
        return {"n": len(s), "min": s[0], "median": median, "max": s[-1],
                "mean": sum(s) / len(s)}
    return {"dropped": _summ(dropped_lengths), "retained": _summ(retained_lengths)}


# ---------- the committed, code-free artifact and its replay ----------

@dataclass(frozen=True)
class NoteTally:
    """One arm's verdict TALLIES for one note. Deliberately carries no codes:
    a per-encounter diagnosis code is clinical data (spec §5e), so the codes
    live in the gitignored roster and the committed artifact holds only counts
    the rates recompute from."""
    verified: int
    not_found: int
    unchecked: int
    cause1: int
    cause2: int
    cause3: int
    cause4: int
    input_tokens: int
    output_tokens: int
    latency_ms: int | None

    def as_dict(self) -> dict:
        return dict(verified=self.verified, not_found=self.not_found,
                    unchecked=self.unchecked, cause1=self.cause1,
                    cause2=self.cause2, cause3=self.cause3, cause4=self.cause4,
                    input_tokens=self.input_tokens,
                    output_tokens=self.output_tokens, latency_ms=self.latency_ms)


def tally_from_deduped(deduped: list[DedupedCode], *, input_tokens: int,
                       output_tokens: int, latency_ms: int | None,
                       floor_members=None) -> NoteTally:
    """Turn one note's deduped codes into the committed tally. The single place
    per-note counts are produced, so the artifact and the guard statistics can
    never come from two different countings."""
    d = note_denominators(deduped)
    band = floor_band(deduped, floor_members=floor_members)
    return NoteTally(verified=d.verified, not_found=d.not_found,
                     unchecked=d.unchecked, cause1=band.cause1,
                     cause2=band.cause2, cause3=band.cause3, cause4=band.cause4,
                     input_tokens=input_tokens, output_tokens=output_tokens,
                     latency_ms=latency_ms)


@dataclass(frozen=True)
class AssembledRun:
    """Everything downstream needs, restricted to the intersection."""
    analysis: AnalysisSet
    arm_tallies: dict[str, dict[str, NoteTally]]   # "A"/"B" -> eid -> tally
    agreement: dict[str, float | None]             # descriptive only, never routed
    strata: dict[str, bool]
    note_pairs: list[NotePair]                     # ordered by analysis.ids


def assemble_run(results_a: dict[str, ArmNoteResult],
                 results_b: dict[str, ArmNoteResult],
                 strata: dict[str, bool],
                 floor_members=None) -> AssembledRun:
    """Fold both arms' per-note results into the analysis set and its tallies.

    Pairing follows the SORTED analysis ids for both arms, so a NotePair can
    never pair note i of one arm with note j of the other, which would silently
    destroy the pairing the bootstrap depends on.
    """
    per_note_ok = {
        eid: (results_a[eid].output is not None,
              results_b[eid].output is not None)
        for eid in results_a.keys() & results_b.keys()
    }
    analysis = build_analysis_set(per_note_ok)

    tallies: dict[str, dict[str, NoteTally]] = {"A": {}, "B": {}}
    agreement: dict[str, float | None] = {}
    pairs: list[NotePair] = []

    for eid in analysis.ids:
        ra, rb = results_a[eid], results_b[eid]
        da = dedupe_note(ra.output)
        db = dedupe_note(rb.output)

        ta = tally_from_deduped(da, input_tokens=ra.tokens[0],
                                output_tokens=ra.tokens[1],
                                latency_ms=ra.latency_ms,
                                floor_members=floor_members)
        tb = tally_from_deduped(db, input_tokens=rb.tokens[0],
                                output_tokens=rb.tokens[1],
                                latency_ms=rb.latency_ms,
                                floor_members=floor_members)
        tallies["A"][eid] = ta
        tallies["B"][eid] = tb
        agreement[eid] = note_agreement(da, db)
        pairs.append(NotePair(nf_a=ta.not_found,
                              checkable_a=ta.verified + ta.not_found,
                              nf_b=tb.not_found,
                              checkable_b=tb.verified + tb.not_found))

    return AssembledRun(
        analysis=analysis,
        arm_tallies=tallies,
        agreement=agreement,
        strata={eid: strata[eid] for eid in analysis.ids},
        note_pairs=pairs,
    )


def _rates_from_sums(v: int, nf: int, un: int,
                     c2: int, c3: int, c4: int) -> dict:
    """Pooled rates in points from summed counts. The single arithmetic both
    build and replay use, so a stored rate always recomputes from its tallies."""
    checkable = v + nf
    total = v + nf + un
    vr = None if checkable == 0 else 100.0 * v / checkable
    return {
        "verified_rate": vr,
        "not_found_rate": None if vr is None else 100.0 - vr,
        "pessimistic_verified_rate": None if total == 0 else 100.0 * v / total,
        "unchecked_share": None if total == 0 else 100.0 * un / total,
        "floor_lower": 0.0 if checkable == 0 else 100.0 * (c4 + c3 + c2) / checkable,
        "floor_upper": 0.0 if checkable == 0 else 100.0 * nf / checkable,
        "n_verified": v, "n_not_found": nf, "n_unchecked": un,
        "n_checkable": checkable, "n_codes_deduped": total,
        "floor_cause_counts": {"cause1": (nf - c2 - c3 - c4),
                               "cause2": c2, "cause3": c3, "cause4": c4},
    }


def _pctl(xs: list[int], p: int) -> float | None:
    if not xs:
        return None
    return float(np.percentile(np.array(xs, float), p))


def aggregate_tallies(tallies: list[NoteTally]) -> dict:
    v = sum(t.verified for t in tallies)
    nf = sum(t.not_found for t in tallies)
    un = sum(t.unchecked for t in tallies)
    c2 = sum(t.cause2 for t in tallies)
    c3 = sum(t.cause3 for t in tallies)
    c4 = sum(t.cause4 for t in tallies)
    agg = _rates_from_sums(v, nf, un, c2, c3, c4)
    agg["n_notes"] = len(tallies)
    agg["codes_per_note"] = (agg["n_codes_deduped"] / len(tallies)
                             if tallies else 0.0)
    agg["input_tokens"] = sum(t.input_tokens for t in tallies)
    agg["output_tokens"] = sum(t.output_tokens for t in tallies)
    lat = [t.latency_ms for t in tallies if t.latency_ms is not None]
    agg["latency_notes_contributing"] = len(lat)
    agg["latency_p50"] = _pctl(lat, 50)
    agg["latency_p95"] = _pctl(lat, 95)
    return agg


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_committed_artifact(*, arm_tallies: dict[str, dict[str, NoteTally]],
                             agreement: dict[str, float | None],
                             strata: dict[str, bool], comparison: dict,
                             run_meta: dict, arm_meta: dict) -> dict:
    """The committed, code-free artifact. Per-note tallies + per-arm aggregates
    + the top-level comparison block (spec §6)."""
    arms: dict[str, dict] = {}
    for arm, per_note in arm_tallies.items():
        tallies = list(per_note.values())
        agg = aggregate_tallies(tallies)
        # per-stratum aggregates keyed on plan_empty (spec §3)
        strat: dict[str, dict | None] = {}
        for label, want in (("plan_empty", True), ("plan_nonempty", False)):
            sub = [t for eid, t in per_note.items() if strata.get(eid) is want]
            strat[label] = aggregate_tallies(sub) if sub else None
        arms[arm] = {
            **arm_meta[arm], **agg,
            "prompt_version": _cache_version_string(arm_meta[arm]["requested_effort"]),
            "max_tokens": CODING_MAX_TOKENS,
            "per_stratum": strat,
            "notes": {eid: t.as_dict() for eid, t in per_note.items()},
        }
    return {
        "created_at": _utcnow(),
        **run_meta,
        "agreement": agreement,
        "strata": strata,
        "comparison": comparison,
        "arms": arms,
    }


def build_roster(rows: list[dict]) -> dict:
    """The gitignored .full.json roster: encounter id, arm, systems seen, code,
    model_description, auto-classified cause, blank adjudication column
    (spec §5e). rows are built by the caller from the deduped codes."""
    return {"created_at": _utcnow(),
            "columns": ["encounter_id", "arm", "systems_seen", "code",
                        "model_description", "auto_cause", "adjudication"],
            "rows": rows}


def replay_coding(artifact: Path) -> dict:
    """Recompute every per-arm rate from the stored per-note tallies and refuse
    to agree with a stored aggregate it cannot reproduce. Hard-errors on a
    vocab_version mismatch, because the vocabulary is correctly absent from the
    response cache key but does change the verified rate (spec §6)."""
    payload = json.loads(Path(artifact).read_text(encoding="utf-8"))

    stored_vocab = payload.get("vocab_version")
    if stored_vocab != vocab.VOCAB_VERSION:
        raise ValueError(
            f"Replay refuses: stored vocab_version {stored_vocab!r} differs from "
            f"current {vocab.VOCAB_VERSION!r}. A warm-cache re-run under a bumped "
            f"pin recomputes a different rate from byte-identical responses.")

    out: dict[str, dict] = {}
    for arm, block in payload["arms"].items():
        tallies = [NoteTally(**{**t, "latency_ms": t.get("latency_ms")})
                   for t in block["notes"].values()]
        recomputed = aggregate_tallies(tallies)
        for name in ("verified_rate", "not_found_rate",
                     "pessimistic_verified_rate", "unchecked_share",
                     "floor_lower", "floor_upper"):
            stored = block.get(name)
            got = recomputed[name]
            if stored is None and got is None:
                continue
            if stored is None or got is None or abs(stored - got) > 1e-9:
                raise ValueError(
                    f"Replay does not match for arm {arm} {name}: recomputed "
                    f"{got}, artifact stores {stored}. Edited artifact or a "
                    f"metric change.")
        # Cost/latency are measured-once: verify stored aggregates equal the
        # recomputation from the stored per-note values, not from responses.
        for name in ("input_tokens", "output_tokens",
                     "latency_notes_contributing"):
            if block.get(name) != recomputed[name]:
                raise ValueError(
                    f"Replay: stored {name} for arm {arm} does not match its "
                    f"per-note values.")
        out[arm] = recomputed
    return out
