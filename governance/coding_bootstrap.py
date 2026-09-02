"""Note-level paired BCa bootstrap for the difference in not-found rate.

The numerics live in governance/bootstrap.py, shared with P3-3 drift
detection. This module is the coding benchmark's domain layer over them: what a
unit is (one analysis-set note), what the statistic is (difference of
ratios-of-sums), and the scale the result is published on.

Pure numerical. Everything internal is on the PROPORTION scale; the public
result multiplies d and the CI endpoints by 100 to percentage points at the
return boundary (spec §2, §4).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from governance.bootstrap import Stat, paired_bca
from governance.bootstrap import replicate_deltas as _engine_replicate_deltas


@dataclass(frozen=True)
class NotePair:
    """One analysis-set note's not_found and checkable counts for both arms."""
    nf_a: int
    checkable_a: int
    nf_b: int
    checkable_b: int


@dataclass(frozen=True)
class BootResult:
    d: float                       # point estimate, percentage points
    ci: tuple[float, float]        # 95% BCa interval, percentage points
    seed: int
    replicates: int                # requested
    retained: int                  # replicates that survived (B in z0 denom)
    dropped: int                   # zero-denominator replicates dropped
    acceleration: float
    acceleration_degenerate: bool


def ratio_diff(nf_a_sum: float, ck_a_sum: float,
               nf_b_sum: float, ck_b_sum: float) -> float | None:
    """Difference of ratios-of-sums, proportion scale. None on a zero denom for
    either arm (matches vocab.verified_rate returning None, never 0.0)."""
    if ck_a_sum == 0 or ck_b_sum == 0:
        return None
    return nf_a_sum / ck_a_sum - nf_b_sum / ck_b_sum


def _arrays(pairs: list[NotePair]):
    return (np.array([p.nf_a for p in pairs], float),
            np.array([p.checkable_a for p in pairs], float),
            np.array([p.nf_b for p in pairs], float),
            np.array([p.checkable_b for p in pairs], float))


def _stat(nf_a, ck_a, nf_b, ck_b, idx) -> float | None:
    return ratio_diff(nf_a[idx].sum(), ck_a[idx].sum(),
                      nf_b[idx].sum(), ck_b[idx].sum())


def _stat_fn(pairs: list[NotePair]) -> tuple[int, Stat]:
    """The engine's unit count and statistic for this analysis set."""
    nf_a, ck_a, nf_b, ck_b = _arrays(pairs)
    return len(pairs), lambda idx: _stat(nf_a, ck_a, nf_b, ck_b, idx)


def replicate_deltas(pairs: list[NotePair], rng: np.random.Generator,
                     replicates: int) -> tuple[np.ndarray, int]:
    """Bootstrap replicate deltas (proportion) with SHARED indices per replicate.
    Returns (kept_deltas, dropped_count)."""
    n, stat = _stat_fn(pairs)
    return _engine_replicate_deltas(n, stat, rng, replicates)


def paired_bootstrap_bca(pairs: list[NotePair], seed: int,
                         replicates: int = 10000,
                         alpha: float = 0.05) -> BootResult:
    """95% BCa interval on the paired not-found-rate difference, in points."""
    n, stat = _stat_fn(pairs)

    # Checked here rather than letting the engine's generic message surface:
    # "zero checkable codes" says what is actually wrong with the analysis set,
    # and callers (and tests) match on it.
    if stat(np.arange(n)) is None:
        raise ValueError(
            "analysis set has zero checkable codes for an arm; no point estimate")

    r = paired_bca(n=n, stat=stat, seed=seed, replicates=replicates, alpha=alpha)

    return BootResult(
        d=100.0 * r.d,
        ci=(100.0 * r.ci[0], 100.0 * r.ci[1]),
        seed=r.seed,
        replicates=r.replicates,
        retained=r.retained,
        dropped=r.dropped,
        acceleration=r.acceleration,
        acceleration_degenerate=r.acceleration_degenerate,
    )
