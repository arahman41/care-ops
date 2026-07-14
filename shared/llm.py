"""Claude client wrapper with per-component model and effort routing.

Routing reflects the cost and accuracy analysis for this project:
  - note structuring : Sonnet 5, high effort (headline accuracy metric)
  - prior_auth        : Sonnet 5, high effort (bounded reasoning)
  - care_gap          : Haiku 4.5 (rules-based core, LLM only for phrasing)
  - coding            : Sonnet 5 xhigh by default; benchmark Opus 4.8 and
                        keep the winner on the held-out coding accuracy set
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

from anthropic import Anthropic

from shared.config import settings

_client = Anthropic(api_key=settings.anthropic_api_key)

ROUTING = {
    "structuring": (os.getenv("MODEL_STRUCTURING", "claude-sonnet-5"),
                    os.getenv("EFFORT_STRUCTURING", "high")),
    "prior_auth": (os.getenv("MODEL_PRIOR_AUTH", "claude-sonnet-5"),
                   os.getenv("EFFORT_PRIOR_AUTH", "high")),
    "care_gap": (os.getenv("MODEL_CARE_GAP", "claude-haiku-4-5-20251001"),
                 None),
    "coding": (os.getenv("MODEL_CODING", "claude-sonnet-5"),
               os.getenv("EFFORT_CODING", "xhigh")),
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


def call(component: str, system: str, user: str,
         max_tokens: int = 1500, temperature: float | None = None) -> str:
    """Route a component to its configured model and effort, return text.

    temperature and effort are mutually exclusive at the API: a component with
    an effort level is a reasoning call and does its own sampling. Only the
    effort-free components (the judge) may pin temperature.
    """
    model, effort = ROUTING[component]
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    # Effort is applied only where a level is configured.
    if effort:
        kwargs["output_config"] = {"effort": effort}
    elif temperature is not None:
        kwargs["temperature"] = temperature

    resp = _client.messages.create(**kwargs)

    if resp.stop_reason == "max_tokens":
        raise TruncatedResponseError(component, max_tokens)

    return "".join(block.text for block in resp.content
                   if getattr(block, "type", None) == "text")
