"""Content-addressed cache for LLM calls (P1-4).

This is what lets the headline number be recomputed without re-spending, so
its correctness is a reproducibility concern, not a performance one. The key
must include the model id and the prompt version: if either changes, reusing
an old judgment would silently mix two different experiments into one number.
"""
from __future__ import annotations

from governance.llm_cache import Cache, cache_key


def test_key_is_stable_for_the_same_inputs():
    a = cache_key("judge", "claude-haiku-4-5-20251001", "v1", "payload")
    b = cache_key("judge", "claude-haiku-4-5-20251001", "v1", "payload")
    assert a == b


def test_a_different_model_is_a_different_key():
    # Otherwise a judge swap would silently reuse the old judge's verdicts.
    a = cache_key("judge", "claude-haiku-4-5-20251001", "v1", "payload")
    b = cache_key("judge", "claude-sonnet-5", "v1", "payload")
    assert a != b


def test_a_different_prompt_version_is_a_different_key():
    # Otherwise editing a judge prompt would silently reuse stale verdicts.
    a = cache_key("judge", "claude-haiku-4-5-20251001", "v1", "payload")
    b = cache_key("judge", "claude-haiku-4-5-20251001", "v2", "payload")
    assert a != b


def test_a_different_task_is_a_different_key():
    a = cache_key("decompose", "m", "v1", "payload")
    b = cache_key("judge", "m", "v1", "payload")
    assert a != b


def test_fields_cannot_bleed_into_each_other():
    # A naive concatenation would make ("ab","c") collide with ("a","bc").
    a = cache_key("ab", "c", "v1", "payload")
    b = cache_key("a", "bc", "v1", "payload")
    assert a != b


def test_roundtrip(tmp_path):
    cache = Cache(tmp_path)
    key = cache_key("judge", "m", "v1", "payload")
    assert cache.get(key) is None
    cache.put(key, '{"verdicts": []}')
    assert cache.get(key) == '{"verdicts": []}'


def test_a_second_cache_over_the_same_root_sees_the_first_one_s_writes(tmp_path):
    # A warm cache across processes is the whole point: replay costs nothing.
    Cache(tmp_path).put(cache_key("t", "m", "v1", "p"), "value")
    assert Cache(tmp_path).get(cache_key("t", "m", "v1", "p")) == "value"
