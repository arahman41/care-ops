"""Paired BCa bootstrap, statistic-agnostic.

Extracted from coding_bootstrap.py so P2-4's coding comparison and P3-3's
drift detection share one implementation. Two hand-rolled BCa engines in one
repo would be free to diverge, and the day one got a fix the other did not,
the two published numbers would stop being comparable with nothing to detect
it.

The caller supplies stat(idx) -> float | None over a resample index. None means
the replicate is undefined (a zero denominator) and is dropped, never coerced
to 0.0, because a dropped replicate and a replicate that measured no difference
are different facts.

BCa is hand-rolled rather than taken from scipy: scipy's uses the naive
strict-< bias correction and does not implement the mid-rank tie convention
pinned here, which matters when a discrete statistic ties with its own point
estimate often, as an identical-arms comparison does on every replicate.

Everything is on the caller's native scale. Rescaling happens at the caller's
return boundary, not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.stats import norm

Stat = Callable[[np.ndarray], float | None]


@dataclass(frozen=True)
class BcaResult:
    d: float
    ci: tuple[float, float]
    seed: int
    replicates: int                # requested
    retained: int                  # replicates that survived (B in the z0 denom)
    dropped: int                   # undefined replicates dropped
    acceleration: float
    acceleration_degenerate: bool


def replicate_deltas(n: int, stat: Stat, rng: np.random.Generator,
                     replicates: int) -> tuple[np.ndarray, int]:
    """Replicate statistics with ONE shared index vector per replicate.

    Shared, not drawn per arm: the pairing is the entire reason this is more
    sensitive than a two-sample test. Drawing independently would discard it
    and widen the interval.

    Returns (kept_deltas, dropped_count).
    """
    kept: list[float] = []
    dropped = 0
    for _ in range(replicates):
        s = stat(rng.integers(0, n, size=n))
        if s is None:
            dropped += 1
        else:
            kept.append(s)
    return np.array(kept, float), dropped


def paired_bca(*, n: int, stat: Stat, seed: int, replicates: int = 10000,
               alpha: float = 0.05) -> BcaResult:
    """BCa interval on a paired difference, on the statistic's own scale.

    The point estimate is computed BEFORE the generator is constructed, and
    each replicate draws exactly one index vector. Both facts are part of the
    published P2-4 numbers: changing either changes the draw order and moves
    an interval that a routing decision rests on.
    """
    full = np.arange(n)

    d = stat(full)
    if d is None:
        raise ValueError("the statistic is undefined on the full sample; "
                         "there is no point estimate to build an interval "
                         "around")

    rng = np.random.default_rng(seed)
    deltas, dropped = replicate_deltas(n, stat, rng, replicates)
    B = len(deltas)
    if B == 0:
        raise ValueError("every bootstrap replicate was dropped (zero denom)")

    # z0: mid-rank tie convention over the RETAINED replicates.
    less = float(np.sum(deltas < d))
    eq = float(np.sum(deltas == d))
    z0 = norm.ppf((less + 0.5 * eq) / B)

    # Acceleration: leave-one-out jackknife over the units being resampled.
    jack = [stat(np.delete(full, i)) for i in range(n)]
    jack = np.array([j for j in jack if j is not None], float)
    u = jack.mean() - jack
    num = float(np.sum(u ** 3))
    den = 6.0 * float(np.sum(u ** 2)) ** 1.5
    if den == 0.0:
        a, a_degenerate = 0.0, True
    else:
        a, a_degenerate = num / den, False

    # BCa-adjusted percentiles.
    def _adj(z_alpha: float) -> float:
        num_z = z0 + z_alpha
        return float(norm.cdf(z0 + num_z / (1.0 - a * num_z)))

    a1 = _adj(norm.ppf(alpha / 2.0))
    a2 = _adj(norm.ppf(1.0 - alpha / 2.0))
    lo = float(np.quantile(deltas, a1, method="linear"))
    hi = float(np.quantile(deltas, a2, method="linear"))

    return BcaResult(
        d=float(d),
        ci=(lo, hi),
        seed=seed,
        replicates=replicates,
        retained=B,
        dropped=dropped,
        acceleration=a,
        acceleration_degenerate=a_degenerate,
    )
