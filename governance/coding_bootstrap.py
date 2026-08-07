"""Note-level paired BCa bootstrap for the difference in not-found rate.

Pure numerical. Everything internal is on the PROPORTION scale; the public
result multiplies d and the CI endpoints by 100 to percentage points at the
return boundary (spec §2, §4). BCa is hand-rolled: scipy's BCa uses the naive
strict-< bias correction and does not implement the mid-rank tie convention
this benchmark pins.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


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


def replicate_deltas(pairs: list[NotePair], rng: np.random.Generator,
                     replicates: int) -> tuple[np.ndarray, int]:
    """Bootstrap replicate deltas (proportion) with SHARED indices per replicate.
    Returns (kept_deltas, dropped_count)."""
    nf_a, ck_a, nf_b, ck_b = _arrays(pairs)
    n = len(pairs)
    kept: list[float] = []
    dropped = 0
    for _ in range(replicates):
        idx = rng.integers(0, n, size=n)          # one draw, used for BOTH arms
        s = _stat(nf_a, ck_a, nf_b, ck_b, idx)
        if s is None:
            dropped += 1
        else:
            kept.append(s)
    return np.array(kept, float), dropped


def paired_bootstrap_bca(pairs: list[NotePair], seed: int,
                         replicates: int = 10000,
                         alpha: float = 0.05) -> BootResult:
    """95% BCa interval on the paired not-found-rate difference, in points."""
    nf_a, ck_a, nf_b, ck_b = _arrays(pairs)
    n = len(pairs)
    full = np.arange(n)

    d_prop = _stat(nf_a, ck_a, nf_b, ck_b, full)
    if d_prop is None:
        raise ValueError(
            "analysis set has zero checkable codes for an arm; no point estimate")

    rng = np.random.default_rng(seed)
    deltas, dropped = replicate_deltas(pairs, rng, replicates)
    B = len(deltas)
    if B == 0:
        raise ValueError("every bootstrap replicate was dropped (zero denom)")

    # z0: mid-rank tie convention over the RETAINED replicates.
    less = float(np.sum(deltas < d_prop))
    eq = float(np.sum(deltas == d_prop))
    z0 = norm.ppf((less + 0.5 * eq) / B)

    # Acceleration: leave-one-note-out jackknife over the analysis set.
    jack = [_stat(nf_a, ck_a, nf_b, ck_b, np.delete(full, i)) for i in range(n)]
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

    return BootResult(
        d=100.0 * d_prop,
        ci=(100.0 * lo, 100.0 * hi),
        seed=seed,
        replicates=replicates,
        retained=B,
        dropped=dropped,
        acceleration=a,
        acceleration_degenerate=a_degenerate,
    )
