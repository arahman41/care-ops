"""The P1-4 note-structuring accuracy harness.

Runs the real intake structuring path over the frozen held-out set, grades it
with a pinned judge, prints the measured number, writes an eval_runs row, and
emits an artifact the number can be recomputed from offline.

The artifact is emitted twice, on purpose:

  <run>.json        committed. Per-fact verdicts, counts, metrics, the split
                    digest, the model ids and the prompt versions. Carries NO
                    clinical text, because data/ is gitignored under the
                    project's no-clinical-data-in-git rule, and a metric
                    artifact is no exception. The metric recomputes from the
                    verdicts alone, so nothing is lost.

  <run>.full.json   local only, gitignored. Adds the fact text and the
                    generated notes, so the judge can be hand-audited.

`replay()` recomputes the counts and the metrics from the committed verdicts
and refuses to agree with a stored metric it cannot reproduce. That is what
makes the headline number a fact rather than a claim, and it is what lets CI
regression-test the metric for free.
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from governance.aci_sections import SOAP_BUCKETS, bucket_sections
from governance.evaluate import StructuringCounts, score_structuring
from governance.facts import (
    PROMPT_VERSION as DECOMPOSE_PROMPT_VERSION,
    Fact,
    decompose_freetext,
    decompose_reference,
    decompose_soap,
)
from governance.heldout import (
    DEFAULT_LOCK_PATH,
    PRIMOCK_DATASET_REF,
    HeldoutExample,
)
from governance.judge import (
    PRESENCE_PROMPT_VERSION,
    SUPPORT_PROMPT_VERSION,
    PresenceVerdict,
    judge_presence,
    judge_support,
)
from governance.llm_cache import Cache, cache_key
from services.intake.structure import MAX_TOKENS, SYSTEM_PROMPT, structure_note
from shared.llm import ROUTING
from shared.schemas import SoapNote

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = REPO_ROOT / "governance" / "eval_artifacts"
CACHE_DIR = REPO_ROOT / "governance" / ".cache"

AGENT_NAME = "note_structuring"


def generate_soap(transcript: str, cache: Cache) -> tuple[SoapNote, str, str | None]:
    """Run the REAL intake structuring path, cached.

    Deliberately calls services.intake.structure.structure_note rather than
    reimplementing it, so the harness scores the system that ships and not a
    copy of it that could drift away from production.

    The cache key covers the whole generation configuration: the model, the
    effort level, the system prompt, and the output budget. Every one of those
    can change what the model writes, so changing any of them is a cache miss.
    A headline number computed half under one configuration and half under
    another is not a measurement of anything, and max_tokens is in here
    specifically because it already bit us once: the original 1200-token cap
    silently truncated long encounters.
    """
    model, effort = ROUTING["structuring"]
    version = f"{effort}|{hash_prompt(SYSTEM_PROMPT)}|max{MAX_TOKENS}"
    key = cache_key("structure", model, version, transcript)

    cached = cache.get(key)
    if cached is not None:
        return SoapNote.model_validate_json(cached), model, effort

    note, model, effort = structure_note(transcript)
    cache.put(key, note.model_dump_json())
    return note, model, effort


def hash_prompt(prompt: str) -> str:
    """Short fingerprint of a prompt, so an edit invalidates its cache."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


@dataclass
class ExampleResult:
    encounter_id: str
    fused: bool
    soap: SoapNote
    model: str                   # the model that actually structured this note
    effort: str | None
    ref_verdicts: list[PresenceVerdict]
    gen_fact_texts: list[str]
    gen_supported: list[bool]

    @property
    def counts(self) -> StructuringCounts:
        return StructuringCounts(
            ref_facts=len(self.ref_verdicts),
            captured=sum(v.found for v in self.ref_verdicts),
            correctly_placed=sum(v.correctly_placed for v in self.ref_verdicts),
            gen_facts=len(self.gen_fact_texts),
            supported=sum(self.gen_supported),
        )


@dataclass
class RunResult:
    dataset_ref: str
    structuring_model: str
    structuring_effort: str | None
    split_digest: str
    examples: list[ExampleResult] = field(default_factory=list)

    # PriMock57's reference notes are free-text GP shorthand with no section
    # headers, so there is no ground truth for where a fact belongs and
    # placement cannot be scored. False means eval_runs.accuracy is written
    # NULL rather than filled with a number that does not mean what the column
    # says it means.
    placement_scored: bool = True

    # PriMock57 only: recall of the human-authored `highlights` key concepts.
    highlights_found: int = 0
    highlights_total: int = 0

    @property
    def counts(self) -> StructuringCounts:
        total = StructuringCounts(0, 0, 0, 0, 0)
        for ex in self.examples:
            total = total + ex.counts
        return total

    @property
    def metrics(self) -> dict:
        m = score_structuring(self.counts)
        if not self.placement_scored:
            # Every bucket was acceptable, so "placement accuracy" here would
            # be a tautological 1.0. Say nothing rather than say something false.
            m["accuracy"] = None
        return m

    @property
    def highlights_recall(self) -> float | None:
        if not self.highlights_total:
            return None
        return self.highlights_found / self.highlights_total

    @property
    def fused_notes(self) -> int:
        return sum(ex.fused for ex in self.examples)

    @property
    def strict_metrics(self) -> dict:
        """Metrics over only the notes whose reference separates A from P.

        The A/P leniency applies to 51 of the 120 held-out notes. Reporting the
        strict subset alongside the headline keeps that leniency visible rather
        than buried.
        """
        total = StructuringCounts(0, 0, 0, 0, 0)
        for ex in self.examples:
            if not ex.fused:
                total = total + ex.counts
        return score_structuring(total)

    @property
    def strict_n(self) -> int:
        return sum(not ex.fused for ex in self.examples)


def _evaluate_one(example: HeldoutExample, cache: Cache) -> ExampleResult:
    soap, model, effort = generate_soap(example.transcript, cache)

    ref_facts = decompose_reference(example.reference_note, cache)
    gen_facts = decompose_soap(soap, cache)
    gen_texts = [f.text for f in gen_facts]

    ref_verdicts = judge_presence(soap, ref_facts, cache)
    gen_supported = judge_support(example.transcript, gen_texts, cache)

    fused = any(s.is_fused for s in bucket_sections(example.reference_note))

    return ExampleResult(
        encounter_id=example.encounter_id,
        fused=fused,
        soap=soap,
        model=model,
        effort=effort,
        ref_verdicts=ref_verdicts,
        gen_fact_texts=gen_texts,
        gen_supported=gen_supported,
    )


def locked_digest(lock_path: Path = DEFAULT_LOCK_PATH) -> str:
    """The split digest recorded in the committed lock file.

    Provenance, not verification. A metric artifact that does not name the
    split it was scored against is unmoored, so this is never optional. The
    real guard, recomputing the split from the datasets and comparing, is
    verify_split() and runs in the CLI before any scoring starts.
    """
    return json.loads(Path(lock_path).read_text(encoding="utf-8"))["digest"]


def evaluate_examples(examples: list[HeldoutExample], cache: Cache,
                      workers: int = 8, dataset_ref: str = "aci-bench-heldout-v1",
                      split_digest: str | None = None, on_done=None) -> RunResult:
    """Score every held-out example. Encounters are independent, so fan out.

    A failure in any encounter re-raises. A partial run that silently skipped
    the encounters it could not handle would report a number over a set nobody
    chose, which is exactly the kind of quiet lie this harness exists to avoid.
    """
    results: list[ExampleResult] = []

    if examples:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_evaluate_one, ex, cache) for ex in examples]
            for future in as_completed(futures):
                result = future.result()      # re-raises
                results.append(result)
                if on_done:
                    on_done(result)

        # Stable order regardless of thread completion order.
        results.sort(key=lambda r: r.encounter_id)

    # Take the model from the pipeline that actually ran, not from ROUTING, so
    # the artifact records what produced the number rather than what we assume.
    if results:
        model, effort = results[0].model, results[0].effort
    else:
        model, effort = ROUTING["structuring"]

    return RunResult(
        dataset_ref=dataset_ref,
        structuring_model=model,
        structuring_effort=effort,
        split_digest=split_digest if split_digest is not None else locked_digest(),
        examples=results,
    )


# ---------- PriMock57: audio in, end to end (the Phase 1 exit gate) ----------

def transcribe_primock(example: HeldoutExample, cache: Cache,
                       model_size: str = "base") -> str:
    """Whisper the two speaker tracks into one dialogue, cached.

    Transcription is the slow part of the gate (over two hours of audio across
    the held-out consultations), so the result is cached like any other model
    output and a re-run replays it for free.
    """
    key = cache_key("transcribe", f"whisper-{model_size}", "v1",
                    "|".join(str(p) for p in example.audio))

    cached = cache.get(key)
    if cached is not None:
        return cached

    from services.intake.transcribe import transcribe_consultation

    doctor, patient = example.audio
    transcript = transcribe_consultation(str(doctor), str(patient),
                                         model_size=model_size)
    cache.put(key, transcript)
    return transcript


def _evaluate_primock_one(example: HeldoutExample, cache: Cache,
                          model_size: str) -> tuple[ExampleResult, int, int]:
    transcript = transcribe_primock(example, cache, model_size)
    soap, model, effort = generate_soap(transcript, cache)

    # No section headers in a GP note, so every bucket is acceptable and
    # placement is not scored. See decompose_freetext.
    ref_facts = decompose_freetext(example.reference_note, cache)
    gen_facts = decompose_soap(soap, cache)
    gen_texts = [f.text for f in gen_facts]

    ref_verdicts = judge_presence(soap, ref_facts, cache)
    gen_supported = judge_support(transcript, gen_texts, cache)

    # The human-authored key concepts: the closest thing PriMock57 has to a
    # gold list of what the note must not miss.
    highlight_facts = [
        Fact(text=h, acceptable=frozenset(SOAP_BUCKETS), source_header="highlight")
        for h in example.highlights
    ]
    highlight_verdicts = judge_presence(soap, highlight_facts, cache)
    found = sum(v.found for v in highlight_verdicts)

    result = ExampleResult(
        encounter_id=example.encounter_id,
        fused=False,
        soap=soap,
        model=model,
        effort=effort,
        ref_verdicts=ref_verdicts,
        gen_fact_texts=gen_texts,
        gen_supported=gen_supported,
    )
    return result, found, len(highlight_facts)


def evaluate_primock(examples: list[HeldoutExample], cache: Cache,
                     model_size: str = "base", workers: int = 4,
                     on_done=None) -> RunResult:
    """Score the PriMock57 held-out consultations from audio.

    This is the Phase 1 exit gate's end-to-end path: wav files in, Whisper,
    the real structuring prompt, then the same fact-level judging the ACI-Bench
    headline uses. Placement is not scored (the GP notes are not sectioned), so
    the report and the eval_runs row both say so instead of quietly reporting a
    1.0 that means nothing.
    """
    results: list[ExampleResult] = []
    found = total = 0

    if examples:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_evaluate_primock_one, ex, cache, model_size)
                       for ex in examples]
            for future in as_completed(futures):
                result, n_found, n_total = future.result()
                results.append(result)
                found += n_found
                total += n_total
                if on_done:
                    on_done(result)

        results.sort(key=lambda r: r.encounter_id)

    if results:
        model, effort = results[0].model, results[0].effort
    else:
        model, effort = ROUTING["structuring"]

    return RunResult(
        dataset_ref=PRIMOCK_DATASET_REF,
        structuring_model=model,
        structuring_effort=effort,
        split_digest=locked_digest(),
        examples=results,
        placement_scored=False,
        highlights_found=found,
        highlights_total=total,
    )


# ---------- artifacts ----------

def _redacted(result: RunResult) -> dict:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_ref": result.dataset_ref,
        "split_digest": result.split_digest,
        "structuring_model": result.structuring_model,
        "structuring_effort": result.structuring_effort,
        "judge_model": ROUTING["eval_judge"][0],
        "prompt_versions": {
            "structuring": hash_prompt(SYSTEM_PROMPT),
            "decompose": DECOMPOSE_PROMPT_VERSION,
            "presence": PRESENCE_PROMPT_VERSION,
            "support": SUPPORT_PROMPT_VERSION,
        },
        "n_examples": len(result.examples),
        "fused_ap_notes": result.fused_notes,
        "strict_n": result.strict_n,
        "placement_scored": result.placement_scored,
        "highlights_found": result.highlights_found,
        "highlights_total": result.highlights_total,
        "highlights_recall": result.highlights_recall,
        "counts": result.counts.__dict__,
        "metrics": result.metrics,
        "strict_metrics": result.strict_metrics,
        "examples": [
            {
                "encounter_id": ex.encounter_id,
                "fused": ex.fused,
                # Verdicts only. No clinical text: data/ is gitignored and a
                # metric artifact does not get an exemption.
                "ref": [
                    {
                        "acceptable": sorted(v.fact.acceptable),
                        "found": v.found,
                        "section": v.section,
                    }
                    for v in ex.ref_verdicts
                ],
                "gen": ex.gen_supported,
            }
            for ex in result.examples
        ],
    }


def _full(result: RunResult) -> dict:
    payload = _redacted(result)
    for ex_payload, ex in zip(payload["examples"], result.examples):
        ex_payload["soap"] = ex.soap.model_dump()
        for entry, verdict in zip(ex_payload["ref"], ex.ref_verdicts):
            entry["fact"] = verdict.fact.text
            entry["source_header"] = verdict.fact.source_header
        ex_payload["gen"] = [
            {"fact": text, "supported": supported}
            for text, supported in zip(ex.gen_fact_texts, ex.gen_supported)
        ]
    return payload


def write_artifacts(result: RunResult, out_dir: Path = ARTIFACT_DIR) -> Path:
    """Write the committed artifact and the local full-text one. Returns the former."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = result.dataset_ref.replace("/", "-")

    committed = out_dir / f"structuring_{slug}_{stamp}.json"
    committed.write_text(json.dumps(_redacted(result), indent=2, sort_keys=True),
                         encoding="utf-8")

    full = out_dir / f"structuring_{slug}_{stamp}.full.json"
    full.write_text(json.dumps(_full(result), indent=2, sort_keys=True),
                    encoding="utf-8")

    return committed


def replay(artifact: Path) -> dict:
    """Recompute the headline metrics from a committed artifact. No API calls.

    This RECOMPUTES from the per-fact verdicts rather than reading the stored
    metrics back, then asserts the two agree. If they ever disagree, either the
    artifact was edited or the metric math changed under it, and both are
    things you want to hear about loudly.
    """
    payload = json.loads(Path(artifact).read_text(encoding="utf-8"))

    total = StructuringCounts(0, 0, 0, 0, 0)
    for ex in payload["examples"]:
        ref = ex["ref"]
        gen = ex["gen"]
        supported = sum(
            g["supported"] if isinstance(g, dict) else bool(g) for g in gen)
        total = total + StructuringCounts(
            ref_facts=len(ref),
            captured=sum(1 for f in ref if f["found"]),
            correctly_placed=sum(
                1 for f in ref if f["found"] and f["section"] in f["acceptable"]),
            gen_facts=len(gen),
            supported=supported,
        )

    metrics = score_structuring(total)

    stored = payload["metrics"]
    for name, value in metrics.items():
        # accuracy is NULL for PriMock57, where placement is not scorable.
        # A metric that was never claimed cannot be contradicted.
        if stored.get(name) is None:
            continue
        if abs(stored[name] - value) > 1e-9:
            raise ValueError(
                f"Replay does not match the artifact: recomputed {name}="
                f"{value:.6f} but the artifact stores {stored[name]:.6f}. The "
                f"artifact has been edited, or the metric math changed under "
                f"it. Either way the stored number is no longer trustworthy.")

    return {"counts": total, "metrics": metrics, "payload": payload}
