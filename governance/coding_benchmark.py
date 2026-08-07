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

import time
from dataclasses import dataclass

from governance.aci_sections import SOAP_BUCKETS, bucket_sections
from governance.llm_cache import Cache, cache_key
from governance.structuring_eval import hash_prompt
from services.agent_coding.agent import (
    CodingError, _MAX_TOKENS as CODING_MAX_TOKENS, _SYSTEM as CODING_SYSTEM,
    parse_and_enrich,
)
from shared.llm import LLMResult, TruncatedResponseError, call_detailed
from shared.schemas import SoapNote


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
