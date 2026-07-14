"""The judge: the only place a model grades this project's headline number.

Two questions, one cached call each per encounter.

    judge_presence  Was each REFERENCE fact captured, and in which SOAP
                    section? This yields recall and section-placement.
    judge_support   Is each GENERATED fact supported by the TRANSCRIPT? This
                    yields precision, and its complement is the hallucination
                    rate, which is the direct test of the P1-2 prompt's
                    promise never to invent findings.

Every judge response is validated against the input before it is believed. The
failure mode that matters is not a crash, it is a plausible lie: if the judge
returns fewer verdicts than there were facts and the harness zips them
together, reference facts silently vanish from the recall denominator and the
score goes UP. A quietly inflated headline metric is worse than a broken one,
so a protocol violation raises rather than degrades.

The judge is pinned to Haiku 4.5 at temperature 0 and its model id and prompt
version are part of the cache key, so swapping either is a cache miss, never a
silent mix of two experiments.
"""
from __future__ import annotations

from dataclasses import dataclass

from governance.aci_sections import SOAP_BUCKETS
from governance.facts import Fact
from governance.llm_cache import Cache, cache_key
from shared.llm import call, extract_json, ROUTING
from shared.schemas import SoapNote

# Bump on any prompt edit. Part of the cache key.
PRESENCE_PROMPT_VERSION = "v1"
SUPPORT_PROMPT_VERSION = "v1"

_PRESENCE_TASK = "judge_presence"
_SUPPORT_TASK = "judge_support"

_PRESENCE_SYSTEM = (
    "You check whether each clinical fact from a reference note appears in a "
    "generated SOAP note, and if so, in which section.\n"
    "You are given the generated note's four sections and a numbered list of "
    "reference facts.\n"
    "Return ONLY a JSON array, one object per reference fact, in the same "
    "order, with the same ids:\n"
    '  [{"id": 1, "found": true, "section": "subjective"}, '
    '{"id": 2, "found": false, "section": null}]\n'
    "found is true when the generated note states that fact, in any wording. "
    "A paraphrase counts. A fact that is merely implied does not count.\n"
    "section is the section of the generated note where the fact appears, one "
    "of subjective, objective, assessment, plan. It must be null when found is "
    "false, and it must be a section name when found is true.\n"
    "Return exactly one object per reference fact. Never omit one."
)

_SUPPORT_SYSTEM = (
    "You check whether each clinical fact is supported by a consultation "
    "transcript.\n"
    "You are given the transcript and a numbered list of facts taken from a "
    "generated clinical note.\n"
    "Return ONLY a JSON array, one object per fact, in the same order, with "
    "the same ids:\n"
    '  [{"id": 1, "supported": true}, {"id": 2, "supported": false}]\n'
    "supported is true when the transcript states or directly evidences the "
    "fact. It is false when the fact was invented, inferred, or assumed and "
    "the transcript does not support it.\n"
    "Judge only against the transcript. A fact can be clinically plausible and "
    "still be unsupported.\n"
    "Return exactly one object per fact. Never omit one."
)


class JudgeProtocolError(RuntimeError):
    """The judge's reply does not line up with what it was asked to judge."""


@dataclass(frozen=True)
class PresenceVerdict:
    """One reference fact's fate in the generated note."""

    fact: Fact
    found: bool
    section: str | None          # where the judge found it, if it did

    @property
    def correctly_placed(self) -> bool:
        """Captured AND filed in a section the reference permits.

        For the 51 held-out notes that fuse assessment and plan, `acceptable`
        holds both, so either answer is correct. Everywhere else it holds one.
        """
        return self.found and self.section in self.fact.acceptable


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {text}" for i, text in enumerate(items, start=1))


def _verdicts(raw: str, n: int, key_name: str) -> list[dict]:
    """Parse and validate the judge's reply against the question it was asked.

    Guards, in order of how badly each would corrupt the number:
      - a short reply would shrink the denominator and inflate the score
      - duplicate or missing ids would misalign verdicts with facts
      - a missing boolean would be silently falsey
    """
    data = extract_json(raw)
    if not isinstance(data, list):
        raise JudgeProtocolError(
            f"Judge must return a JSON array, got {type(data).__name__}. "
            f"Raw: {raw[:200]!r}")

    if len(data) != n:
        raise JudgeProtocolError(
            f"Judge returned {len(data)} verdicts for {n} facts. Refusing to "
            f"zip them: dropping a fact would shrink the denominator and "
            f"inflate the score. Raw: {raw[:200]!r}")

    ids = [item.get("id") for item in data]
    if sorted(ids) != list(range(1, n + 1)):
        raise JudgeProtocolError(
            f"Judge returned ids {ids}, expected exactly 1..{n}. Verdicts "
            f"cannot be matched to facts.")

    by_id = {item["id"]: item for item in data}
    ordered = [by_id[i] for i in range(1, n + 1)]

    for item in ordered:
        if not isinstance(item.get(key_name), bool):
            raise JudgeProtocolError(
                f"Judge verdict {item!r} has no boolean {key_name!r}.")

    return ordered


def judge_presence(soap: SoapNote, facts: list[Fact],
                   cache: Cache) -> list[PresenceVerdict]:
    """Was each reference fact captured, and in which section? Recall + placement."""
    if not facts:
        return []

    generated = (
        f"SUBJECTIVE\n{soap.subjective}\n\n"
        f"OBJECTIVE\n{soap.objective}\n\n"
        f"ASSESSMENT\n{soap.assessment}\n\n"
        f"PLAN\n{soap.plan}"
    )
    payload = (
        f"GENERATED SOAP NOTE\n{generated}\n\n"
        f"REFERENCE FACTS\n{_numbered([f.text for f in facts])}"
    )

    model, _ = ROUTING["eval_judge"]
    key = cache_key(_PRESENCE_TASK, model, PRESENCE_PROMPT_VERSION, payload)

    raw = cache.get(key)
    if raw is None:
        raw = call("eval_judge", system=_PRESENCE_SYSTEM, user=payload,
                   max_tokens=4000, temperature=0)
        cache.put(key, raw)

    ordered = _verdicts(raw, len(facts), "found")

    out: list[PresenceVerdict] = []
    for fact, item in zip(facts, ordered):
        found = item["found"]
        section = item.get("section")

        if found and section not in SOAP_BUCKETS:
            raise JudgeProtocolError(
                f"Judge said a fact was found but gave section {section!r}, "
                f"which is not one of {SOAP_BUCKETS}. A placement metric "
                f"cannot use a verdict that will not say where.")
        if not found and section is not None:
            section = None      # a section on a not-found fact is meaningless

        out.append(PresenceVerdict(fact=fact, found=found, section=section))

    return out


def judge_support(transcript: str, gen_facts: list[str],
                  cache: Cache) -> list[bool]:
    """Is each generated fact supported by the transcript? Precision.

    The complement is the hallucination rate. This is scored against the
    transcript rather than the clinician note on purpose: the note is a
    selective summary, so a generated fact that is in the transcript but not
    in the note is a legitimate inclusion, not an error.
    """
    if not gen_facts:
        return []

    payload = (
        f"TRANSCRIPT\n{transcript}\n\n"
        f"FACTS FROM THE GENERATED NOTE\n{_numbered(gen_facts)}"
    )

    model, _ = ROUTING["eval_judge"]
    key = cache_key(_SUPPORT_TASK, model, SUPPORT_PROMPT_VERSION, payload)

    raw = cache.get(key)
    if raw is None:
        raw = call("eval_judge", system=_SUPPORT_SYSTEM, user=payload,
                   max_tokens=4000, temperature=0)
        cache.put(key, raw)

    ordered = _verdicts(raw, len(gen_facts), "supported")
    return [item["supported"] for item in ordered]
