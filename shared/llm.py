"""Claude client wrapper with per-component model and effort routing.

Routing reflects the cost and accuracy analysis for this project:
  - note structuring : Sonnet 5, high effort (headline accuracy metric)
  - prior_auth        : Sonnet 5, high effort (bounded reasoning)
  - care_gap          : Haiku 4.5 (rules-based core, LLM only for phrasing)
  - coding            : Opus 4.8 at high, chosen by the P2-4 benchmark.
                        That benchmark compared VERIFIED RATES, not coding
                        accuracy: no held-out set carries gold billing
                        codes, so there is no coding accuracy to measure.
  - transparency      : Haiku 4.5 (template fill)
  - eval_judge        : Haiku 4.5 at temperature 0. This one grades the
                        others, so it is pinned hard: no effort, no sampling.
                        Changing this model changes the headline number, so
                        the model id is part of the eval cache key (see
                        governance/llm_cache.py) and a swap is a cache miss
                        rather than a silent blend of two experiments.

Effort is passed via output_config={"effort": ...}, confirmed against
anthropic-sdk-python 0.116.0. This differs from the extra_body approach in
earlier drafts; the SDK now has a first-class output_config parameter.

Prompt caching is deliberately not used here. Every system prompt in this
project is a few hundred tokens, well under the cache minimum, so a
cache_control block would buy nothing and cost clarity. Revisit if the coding
agent's reference tables land, since those are large and stable.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from anthropic import Anthropic

from shared.config import settings

_client = Anthropic(api_key=settings.anthropic_api_key)

# coding: the P2-4 benchmark (artifact
# governance/eval_artifacts/coding_20260807T214249Z.json, branch=inconclusive,
# guard=floor_divergence) routed to claude-opus-4-8 at high. This is a
# (model, effort) CONFIGURATION result, not a model result, and not a
# coding-accuracy result.
#
# The branch was inconclusive, so the rule routes on COST, not on a
# demonstrated quality win: 113 held-out notes gave a paired not-found-rate
# difference of 0.70 points with a 95% BCa CI of [-0.73, 2.22], which neither
# clears zero nor fits inside the pre-registered 1.5 point margin. Cost decided
# it, at $3.16 against $6.01 over the same set. Latency agreed independently
# (p50 15 s against 73 s), and so did the unresolved quality point estimate.
ROUTING = {
    "structuring": (os.getenv("MODEL_STRUCTURING", "claude-sonnet-5"),
                    os.getenv("EFFORT_STRUCTURING", "high")),
    "prior_auth": (os.getenv("MODEL_PRIOR_AUTH", "claude-sonnet-5"),
                   os.getenv("EFFORT_PRIOR_AUTH", "high")),
    "care_gap": (os.getenv("MODEL_CARE_GAP", "claude-haiku-4-5-20251001"),
                 None),
    "coding": (os.getenv("MODEL_CODING", "claude-opus-4-8"),
               os.getenv("EFFORT_CODING", "high")),
    "transparency": (os.getenv("MODEL_TRANSPARENCY", "claude-haiku-4-5-20251001"),
                     None),
    "eval_judge": (os.getenv("MODEL_EVAL_JUDGE", "claude-haiku-4-5-20251001"),
                   None),
}


class TruncatedResponseError(RuntimeError):
    """The model hit max_tokens and its answer was cut off mid-sentence.

    Raised in one place because the symptom is otherwise baffling: a truncated
    JSON object surfaces as "Unterminated string at line 5", which reads like a
    prompt problem and is really a budget problem. The P1-4 harness hit exactly
    this on a long encounter, so every component now fails loudly and says what
    to change.
    """

    def __init__(self, component: str, max_tokens: int):
        super().__init__(
            f"{component!r} hit its {max_tokens}-token output limit and the "
            f"response was truncated. Raise max_tokens for this component. "
            f"Do not parse a truncated response: it is a fragment, not an answer.")


class MalformedJSONError(ValueError):
    """The model returned something that is not the JSON object we asked for.

    `reason` is kept preview-free so a caller can re-wrap this in its own
    error without nesting two copies of the raw output into one message.
    """

    def __init__(self, reason: str, raw: str):
        self.reason = reason
        preview = raw[:200] + ("..." if len(raw) > 200 else "")
        super().__init__(f"{reason}. Raw output: {preview!r}")


def strip_code_fence(raw: str) -> str:
    """Drop a ```json ... ``` wrapper. Claude adds one even when told not to."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw[3:]
        if raw[:4].lower() == "json":
            raw = raw[4:]
        raw = raw.removesuffix("```")
    return raw.strip()


def extract_json(raw: str) -> dict | list:
    """Parse a model response into JSON, tolerating a code fence.

    One place for this, because both the intake structurer and the eval judge
    need it and a divergence between them would be a silent scoring bug.
    """
    cleaned = strip_code_fence(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise MalformedJSONError(f"not valid JSON ({exc})", raw) from exc


# Distinguishes "caller did not override effort" from "override to no effort".
# ROUTING stores None as a MEANINGFUL effort for care_gap/transparency/eval_judge,
# so a plain `effort=None` default would collide those two cases. Harmless for
# P2-4's two arms, but this is the one place model routing lives.
_UNSET = object()


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str            # resp.model: the id that ACTUALLY ran, not requested
    input_tokens: int
    output_tokens: int
    stop_reason: str      # never "max_tokens": call_detailed raises first

    def to_json(self) -> str:
        return json.dumps({
            "text": self.text, "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "stop_reason": self.stop_reason,
        })

    @classmethod
    def from_json(cls, s: str) -> "LLMResult":
        d = json.loads(s)
        return cls(text=d["text"], model=d["model"],
                   input_tokens=d["input_tokens"],
                   output_tokens=d["output_tokens"],
                   stop_reason=d["stop_reason"])


def call_detailed(component: str, system: str, user: str,
                  max_tokens: int = 1500, temperature: float | None = None,
                  model: str | None = None, effort=_UNSET) -> LLMResult:
    """Route a component to a model and effort, return the full result.

    `model` and `effort` override ROUTING for this one call; that is how P2-4
    issues one arm as Sonnet-5-at-xhigh and the other as Opus-4.8-at-high while
    keeping model routing in this one module. `effort` uses the _UNSET sentinel,
    not None, because None is a meaningful configured effort.

    temperature and effort are mutually exclusive at the API: an effort call is
    a reasoning call and samples for itself. Only effort-free components (the
    judge) may pin temperature.
    """
    default_model, default_effort = ROUTING[component]
    model = default_model if model is None else model
    effort = default_effort if effort is _UNSET else effort

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if effort:
        kwargs["output_config"] = {"effort": effort}
    elif temperature is not None:
        kwargs["temperature"] = temperature

    resp = _client.messages.create(**kwargs)

    if resp.stop_reason == "max_tokens":
        raise TruncatedResponseError(component, max_tokens)

    text = "".join(block.text for block in resp.content
                   if getattr(block, "type", None) == "text")
    return LLMResult(
        text=text,
        model=resp.model,                    # what actually ran
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        stop_reason=resp.stop_reason,
    )


def call(component: str, system: str, user: str,
         max_tokens: int = 1500, temperature: float | None = None) -> str:
    """Thin wrapper over call_detailed for callers that want only the text.

    Kept so no existing caller changes: call() returns str and discards the
    response, which is exactly why call_detailed exists (P2-4 needs resp.model
    and resp.usage).
    """
    return call_detailed(component, system, user, max_tokens=max_tokens,
                         temperature=temperature).text
