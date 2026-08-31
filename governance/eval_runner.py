"""P3-1: turn a committed eval artifact into a guarded eval_runs row.

The gate says "scores an agent against the held-out set for a named window and
writes accuracy, F1, precision and recall to eval_runs". Only one agent in this
repo can honor that literally, because only one has labels: note_structuring,
against clinician-written reference notes. coding, care_gap and prior_auth have
no labeled held-out set, so this refuses them by name rather than quietly
scoring something else. The refusal is as much the deliverable as the write is:
it is what stops the gate being satisfied dishonestly later.

Nothing here does metric arithmetic. Scoring is structuring_eval.replay(),
which recomputes the metrics from the per-fact verdicts and raises if its
recomputation disagrees with what the artifact stores. Policy and the write
live in governance/evaluate.py. This module is the orchestration between them,
and the import edge runs one way: eval_runner -> evaluate, never back.

A window is A POINT IN TIME WITH THE GENERATION CONFIGURATION HELD FIXED. The
held-out set, prompt, model and effort do not vary across windows, so drift
measured between them is change on the vendor's side of a hosted model. That
is the reading P3-3 and the HTI-1 framing need, and GenerationConfig is what
makes a violation of it visible in data instead of resting on this paragraph.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from governance.evaluate import (
    record_eval_run,
    resolve_scoreable,
    windows_with_counts,
)
from governance.heldout import SplitDriftError
from governance.structuring_eval import locked_digest, replay

# The four fields that together decide what the model writes. Any of them
# changing makes two runs incomparable, which is why generate_soap folds all
# four into its cache key.
CONFIG_FIELDS = ("model", "effort", "prompt_hash", "max_tokens")


@dataclass(frozen=True)
class GenerationConfig:
    """What produced a run's notes, recorded so two windows can be compared.

    max_tokens is None for any artifact written before P3-1, and None means
    "not recorded by the harness of the day", NOT 8000. The distinction is
    load-bearing: writing today's value onto July's run would be inventing
    evidence about a run nobody can re-inspect.
    """

    model: str
    effort: str | None
    prompt_hash: str
    max_tokens: int | None

    def differing_fields(self, other: "GenerationConfig") -> tuple[str, ...]:
        """Which fields differ. Empty means the two runs are comparable.

        A None max_tokens on exactly one side is reported as DIFFERING, not as
        "unknown, assume equal". P3-3 must treat such a pair as not certified
        comparable and say so, because the run whose cap is unrecorded may
        have been generated under a different one. Both sides None is not a
        difference; it is two equally unprovenanced runs, and that is P3-3's
        problem to describe, not this function's to hide.
        """
        return tuple(f for f in CONFIG_FIELDS
                     if getattr(self, f) != getattr(other, f))


@dataclass(frozen=True)
class ScoredArtifact:
    """A replayed artifact, ready to write. Built without touching a database.

    Separate from the write so the CLI's --no-db path, and the tests that run
    in CI with no Postgres, exercise everything except the INSERT.
    """

    agent_name: str
    window_label: str
    dataset_ref: str
    n_examples: int
    model: str
    model_effort: str | None
    metrics: dict[str, float | None]
    config: GenerationConfig
    measured_at: datetime
    provenance: dict[str, Any]


def config_from_artifact(payload: Mapping[str, Any]) -> GenerationConfig:
    """Read the generation configuration out of a structuring artifact."""
    return GenerationConfig(
        model=payload["structuring_model"],
        effort=payload.get("structuring_effort"),
        prompt_hash=payload["prompt_versions"]["structuring"],
        # .get, not [..]: absent means an artifact from before P3-1 started
        # recording it. See the class docstring.
        max_tokens=payload.get("structuring_max_tokens"),
    )


def _measured_at(payload: Mapping[str, Any], artifact_path: Path) -> datetime:
    """The artifact's own created_at, which is when the measurement happened.

    Missing is a hard error rather than a fallback to now(). A row with no
    honest measurement time is worse than no row: P3-3 orders windows by time,
    so a July run stamped today reads as the newest point on the trend and
    reverses the direction of any drift it is asked to detect.
    """
    raw = payload.get("created_at")
    if not raw:
        raise ValueError(
            f"{artifact_path.name} has no created_at, so there is no honest "
            f"measurement time to write. Refusing to insert a row that would "
            f"be stamped with today's date and read as the newest window.")
    return datetime.fromisoformat(raw)


def _assert_split_matches_lock(payload: Mapping[str, Any],
                               artifact_path: Path) -> None:
    """The artifact must have been measured on the split we still have locked.

    This deliberately is NOT heldout.verify_split(). The two ask different
    questions: verify_split rebuilds the split from data/ and asks whether the
    datasets on disk still reproduce the lock, which is a GENERATION-time
    guard and already runs in scripts/run_structuring_eval.py before a single
    paid call. Ingesting a committed artifact never touches the datasets, so
    requiring them here would make this unrunnable in CI (data/ is gitignored)
    for no gain. Same error type, data-free trigger.
    """
    stored = payload.get("split_digest")
    locked = locked_digest()
    if stored != locked:
        raise SplitDriftError(
            f"{artifact_path.name} was measured against split "
            f"{str(stored)[:12]}... but the committed lock now records "
            f"{locked[:12]}.... Refusing to file it as a window: two windows "
            f"scored on different held-out sets are not a trend, however "
            f"plausible the two numbers look side by side.")


class ReplayedWindowError(RuntimeError):
    """A window that is another window's measurement wearing a new label."""


def _assert_freshly_generated(payload: Mapping[str, Any],
                              artifact_path: Path) -> None:
    """Guard 1, causal: a window must be generated, not served from cache.

    cache_key does not cover the window label, so running a second window
    against the first one's cache is every-call-a-hit: the harness replays the
    earlier notes and reproduces its metrics exactly. Two identical points then
    sit on a drift trend looking like a measurement.

    Structure hits with no structure misses is that, exactly. Artifacts written
    before P3-2 carry no cache_stats at all and are exempt, because window 1
    genuinely was generated; absence of evidence here is not evidence of a
    replay.
    """
    stats = (payload.get("cache_stats") or {}).get("window")
    if not stats:
        return

    hits = (stats.get("hits") or {}).get("structure", 0)
    misses = (stats.get("misses") or {}).get("structure", 0)
    if hits and not misses:
        raise ReplayedWindowError(
            f"{artifact_path.name} was served entirely from cache: "
            f"{hits} structuring cache hits and no misses. Its notes were "
            f"generated by an earlier window, so filing it would put one "
            f"measurement on the trend twice. Point --cache-namespace at a "
            f"fresh window, or use governance.structuring_eval.window_cache.")


def _assert_not_a_duplicate_measurement(scored: "ScoredArtifact") -> None:
    """Guard 2, symptomatic: two windows may not have identical counts.

    Independent generation over 120 encounters does not reproduce all five
    tallies exactly. Identical counts mean a replay or a double-filing.

    Scoped to a DIFFERENT window label on purpose: re-filing is by definition
    the same measurement under the same label, which is what
    scripts/refile_eval_run.py does and must keep doing.
    """
    counts = scored.provenance.get("counts")
    if not counts:
        return

    clashes = [label for label in windows_with_counts(
        scored.agent_name, scored.dataset_ref, counts)
        if label != scored.window_label]
    if clashes:
        raise ReplayedWindowError(
            f"window {scored.window_label!r} has counts identical to "
            f"{', '.join(repr(c) for c in clashes)} on {scored.dataset_ref}. "
            f"Across {scored.n_examples} encounters and "
            f"{counts.get('ref_facts')} reference facts, independent "
            f"generation does not reproduce all five tallies exactly, so this "
            f"is the same measurement filed twice rather than a second "
            f"window. Refusing: two copies of one point are not a trend.")


def prepare_artifact(*, agent_name: str, artifact_path: Path,
                     window_label: str) -> ScoredArtifact:
    """Replay an artifact and build the row, without writing it.

    Order matters. The registry is consulted before any work, so an
    unscoreable agent is refused before the replay rather than after it.
    """
    scoreable = resolve_scoreable(agent_name)

    payload = json.loads(Path(artifact_path).read_text(encoding="utf-8"))

    dataset_ref = payload["dataset_ref"]
    if dataset_ref not in scoreable.dataset_refs:
        raise ValueError(
            f"{agent_name!r} has labels on {list(scoreable.dataset_refs)}, but "
            f"{artifact_path.name} was scored on {dataset_ref!r}. Its labels "
            f"are: {scoreable.labels_are}")

    _assert_split_matches_lock(payload, Path(artifact_path))
    _assert_freshly_generated(payload, Path(artifact_path))

    # replay() recomputes from the per-fact verdicts and raises if it cannot
    # reproduce the stored numbers. P3-1 adds no arithmetic of its own, which
    # is what keeps the headline auditable from one place.
    out = replay(Path(artifact_path))
    metrics = out["metrics"]

    config = config_from_artifact(payload)

    provenance = {
        "generation": asdict(config),
        "artifact": Path(artifact_path).name,
        "split_digest": payload.get("split_digest"),
        "judge_model": payload.get("judge_model"),
        "prompt_versions": payload.get("prompt_versions"),
        # Carried because it is why accuracy is NULL on PriMock57 rows. A
        # reader of eval_runs should be able to tell a declined metric from a
        # missing one without opening the artifact.
        "placement_scored": payload.get("placement_scored", True),
        "counts": out["counts"].__dict__,
        # P3-2. counts is what guard 2 compares later windows against, and
        # cache_stats is the evidence that this window was generated rather
        # than replayed out of another window's namespace.
        "cache_stats": payload.get("cache_stats"),
        "usage": payload.get("usage"),
        "cost_usd": payload.get("cost_usd"),
    }

    return ScoredArtifact(
        agent_name=agent_name,
        window_label=window_label,
        dataset_ref=dataset_ref,
        n_examples=payload["n_examples"],
        model=config.model,
        model_effort=config.effort,
        metrics=metrics,
        config=config,
        measured_at=_measured_at(payload, Path(artifact_path)),
        provenance=provenance,
    )


def score_artifact(*, agent_name: str, artifact_path: Path,
                   window_label: str) -> int:
    """Replay an artifact and file it as one window. Returns the eval_runs id.

    The single scoring entry point. Both the July backfill and every live run
    from scripts/run_structuring_eval.py come through here, so there is one
    path from a measurement to a row, and replay()'s cross-check runs on the
    live path for free.
    """
    return record_scored(prepare_artifact(agent_name=agent_name,
                                          artifact_path=artifact_path,
                                          window_label=window_label))


def record_scored(scored: ScoredArtifact) -> int:
    """Write an already-prepared ScoredArtifact. Returns the eval_runs id.

    Split out so a caller that has already prepared (and printed) a row does
    not replay the artifact a second time just to write it.
    """
    _assert_not_a_duplicate_measurement(scored)
    return record_eval_run(
        agent_name=scored.agent_name,
        model=scored.model,
        model_effort=scored.model_effort,
        window_label=scored.window_label,
        dataset_ref=scored.dataset_ref,
        n_examples=scored.n_examples,
        metrics=scored.metrics,
        provenance=scored.provenance,
        measured_at=scored.measured_at,
    )
