"""Per-model price table for the coding benchmark's cost comparison (spec §8).

governance/pricing.json is a USER-SUPPLIED committed input. An invented table
is the same class of error this re-scope exists to prevent, so an absent table
returns None and the benchmark declines to name a cost winner, leaving
ROUTING["coding"] unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICE_PATH = REPO_ROOT / "governance" / "pricing.json"


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


@dataclass(frozen=True)
class PriceTable:
    source: str
    retrieved: str
    prices: dict[str, ModelPrice]


def load_price_table(path: Path = DEFAULT_PRICE_PATH) -> PriceTable | None:
    """Load the table, or None if the file is absent (a terminal state).

    A present-but-malformed table raises: a bad price is worse than none,
    because it silently produces a wrong cost winner.
    """
    path = Path(path)
    if not path.is_file():
        return None

    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        source = raw["source"]
        retrieved = raw["retrieved"]
        prices = {
            model: ModelPrice(input_per_mtok=float(p["input_per_mtok"]),
                              output_per_mtok=float(p["output_per_mtok"]))
            for model, p in raw["prices"].items()
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"governance/pricing.json is malformed ({exc}). A wrong price routes "
            f"production to the wrong configuration; refusing to guess.") from exc

    if not source or not retrieved or not prices:
        raise ValueError("pricing.json must carry source, retrieved, and prices")
    return PriceTable(source=source, retrieved=retrieved, prices=prices)


def cost_usd(table: PriceTable, model: str,
             input_tokens: int, output_tokens: int) -> float:
    """Cost in USD for a model at a token count. Keyed on the REQUESTED alias
    (claude-sonnet-5, claude-opus-4-8), which is how a plan prices a family."""
    price = table.prices[model]        # KeyError on an unpriced model, loudly
    return (input_tokens / 1e6 * price.input_per_mtok
            + output_tokens / 1e6 * price.output_per_mtok)
