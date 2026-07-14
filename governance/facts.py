"""Decompose clinical text into atomic facts, the unit the metric counts.

The design invariant, which is the reason this module is shaped the way it is:

    A reference fact's SOAP label comes from the human section header it was
    drawn from, never from a model.

So the decomposer is invoked once per section and shown only that section's
body. It never sees the header, so it cannot see, choose, or mis-assign a
label. Batching all sections into one call would be cheaper and would hand the
labelling job to the model, which would quietly turn "the model filed this
fact in the wrong section" into a claim about the judge rather than about the
system under test. The extra calls are tiny, cached, and worth it.

Every call is pinned (Haiku, temperature 0) and cached, so a re-run of the
harness replays byte-identical decompositions and the headline number does not
wobble between runs.
"""
from __future__ import annotations

from dataclasses import dataclass

from governance.aci_sections import (
    ASSESSMENT,
    OBJECTIVE,
    PLAN,
    SOAP_BUCKETS,
    SUBJECTIVE,
    bucket_sections,
)
from governance.llm_cache import Cache, cache_key
from shared.llm import call, extract_json, ROUTING
from shared.schemas import SoapNote

# Bump on any prompt edit. The version is part of the cache key, so a changed
# prompt is a cache miss rather than a silent blend of two experiments.
PROMPT_VERSION = "v1"

_TASK = "decompose"

_SYSTEM = (
    "You split clinical text into atomic facts. "
    "Return ONLY a JSON array of strings, nothing else. "
    "Each string is exactly one atomic clinical fact stated in the input: one "
    "symptom, one finding, one measurement, one history item, one diagnosis, "
    "one medication, or one plan item. "
    "Copy each fact faithfully from the input. Do not add, infer, generalize, "
    "or combine facts. Do not include anything the input does not state. "
    "If the input states no clinical facts, return an empty array."
)


@dataclass(frozen=True)
class Fact:
    """One atomic clinical fact, and where it may legitimately live."""

    text: str
    acceptable: frozenset[str]   # SOAP buckets this fact may occupy
    source_header: str           # the human header it came from, for the audit


def _decompose_text(text: str, cache: Cache) -> list[str]:
    """One cached, pinned call. Returns the raw fact strings."""
    text = text.strip()
    if not text:
        return []

    model, _ = ROUTING["eval_judge"]
    key = cache_key(_TASK, model, PROMPT_VERSION, text)

    raw = cache.get(key)
    if raw is None:
        raw = call("eval_judge", system=_SYSTEM, user=text,
                   max_tokens=1500, temperature=0)
        cache.put(key, raw)

    data = extract_json(raw)
    if not isinstance(data, list):
        raise ValueError(
            f"Decomposer must return a JSON array of fact strings, got "
            f"{type(data).__name__}. Raw: {raw[:200]!r}")

    return [str(item).strip() for item in data if str(item).strip()]


def decompose_reference(note: str, cache: Cache) -> list[Fact]:
    """Atomic facts from a clinician reference note, labelled by human header.

    Each section is decomposed in isolation, so the SOAP label attached to a
    fact is structurally guaranteed to be the one the clinician's own header
    implies.
    """
    out: list[Fact] = []
    for section in bucket_sections(note):
        for text in _decompose_text(section.body, cache):
            out.append(Fact(text=text,
                            acceptable=section.acceptable,
                            source_header=section.header))
    return out


def decompose_freetext(note: str, cache: Cache) -> list[Fact]:
    """Atomic facts from a note that has no section headers.

    PriMock57's reference notes are free-text GP shorthand, not SOAP sections,
    so there is no human header to inherit a label from. Every SOAP bucket is
    therefore acceptable, which makes placement a no-op for these facts. That
    is the honest handling: the harness reports PriMock57 placement accuracy as
    NULL rather than as a meaningless 1.0, because the reference simply does
    not say where a fact belongs.
    """
    return [
        Fact(text=text, acceptable=frozenset(SOAP_BUCKETS), source_header="")
        for text in _decompose_text(note, cache)
    ]


def decompose_soap(soap: SoapNote, cache: Cache) -> list[Fact]:
    """Atomic facts from a generated SOAP note, tagged with the section written.

    These are the units precision is computed over: each one is checked against
    the transcript, and any that the transcript does not support is a
    hallucination.
    """
    sections = (
        (SUBJECTIVE, soap.subjective),
        (OBJECTIVE, soap.objective),
        (ASSESSMENT, soap.assessment),
        (PLAN, soap.plan),
    )

    out: list[Fact] = []
    for bucket, body in sections:
        for text in _decompose_text(body, cache):
            out.append(Fact(text=text,
                            acceptable=frozenset({bucket}),
                            source_header=bucket))
    return out
