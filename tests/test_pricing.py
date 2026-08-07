"""P2-4 price table. Absent table is a terminal state, not a crash (spec §8)."""
from __future__ import annotations

import json

import pytest

from governance.pricing import PriceTable, cost_usd, load_price_table


def _write(tmp_path, obj):
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_absent_table_returns_none(tmp_path):
    assert load_price_table(tmp_path / "nope.json") is None


def test_present_table_loads_with_source_and_date(tmp_path):
    p = _write(tmp_path, {
        "source": "https://example/pricing", "retrieved": "2026-07-22",
        "prices": {"claude-sonnet-5": {"input_per_mtok": 3.0,
                                       "output_per_mtok": 15.0}}})
    table = load_price_table(p)
    assert isinstance(table, PriceTable)
    assert table.source and table.retrieved


def test_malformed_table_raises_rather_than_guessing(tmp_path):
    p = _write(tmp_path, {"prices": {"claude-sonnet-5": {"input_per_mtok": 3.0}}})
    with pytest.raises(ValueError):     # missing output_per_mtok, missing source
        load_price_table(p)


def test_cost_uses_both_input_and_output_prices(tmp_path):
    p = _write(tmp_path, {
        "source": "s", "retrieved": "2026-07-22",
        "prices": {"claude-sonnet-5": {"input_per_mtok": 3.0,
                                       "output_per_mtok": 15.0}}})
    table = load_price_table(p)
    # 1,000,000 input @ $3 + 1,000,000 output @ $15 = $18.
    assert cost_usd(table, "claude-sonnet-5", 1_000_000, 1_000_000) == pytest.approx(18.0)


def test_cost_for_an_unpriced_model_raises(tmp_path):
    p = _write(tmp_path, {
        "source": "s", "retrieved": "2026-07-22",
        "prices": {"claude-sonnet-5": {"input_per_mtok": 3.0,
                                       "output_per_mtok": 15.0}}})
    table = load_price_table(p)
    with pytest.raises(KeyError):
        cost_usd(table, "claude-opus-4-8", 100, 100)
