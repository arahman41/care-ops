"""Claude client wrapper with per-component model and effort routing.

Routing reflects the cost and accuracy analysis for this project:
  - note structuring : Sonnet 5, high effort (headline accuracy metric)
  - prior_auth        : Sonnet 5, high effort (bounded reasoning)
  - care_gap          : Haiku 4.5 (rules-based core, LLM only for phrasing)
  - coding            : Sonnet 5 xhigh by default; benchmark Opus 4.8 and
                        keep the winner on the held-out coding accuracy set
  - transparency      : Haiku 4.5 (template fill)

Stable content (SOAP schema, system prompts, coding references) should be
sent with prompt caching so only the transcript varies per request.

Effort is passed via output_config={"effort": ...}, confirmed against
anthropic-sdk-python 0.116.0. This differs from the extra_body approach in
earlier drafts; the SDK now has a first-class output_config parameter.
"""
from __future__ import annotations

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
}


def call(component: str, system: str, user: str,
         max_tokens: int = 1500) -> str:
    """Route a component to its configured model and effort, return text."""
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
    resp = _client.messages.create(**kwargs)
    return "".join(block.text for block in resp.content
                   if getattr(block, "type", None) == "text")
