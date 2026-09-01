"""Re-score an agent against a held-out labeled set and store the result.

The held-out set is leak-free and never used to tune rules or prompts.
This is the same discipline used in ClinAIQA: measured numbers only.

Two scoring paths live here, and metric arithmetic lives *only* here so it
cannot drift between callers:

  score()             binary classification, for the Phase 2 and 3 agents
  score_structuring() free-text SOAP notes, for the P1-4 headline metric

P3-1 adds a third thing to this file: the policy about which agents are
allowed to have an accuracy at all, and the single guarded writer every
eval_runs INSERT goes through. That lives here rather than in the runner
because it is policy about what a metric is permitted to MEAN, which is this
file's stated domain, and because it keeps the import edge one-way:
eval_runner imports evaluate, never the reverse.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sklearn.metrics import precision_recall_fscore_support, accuracy_score

from governance.heldout import ACI_DATASET_REF, PRIMOCK_DATASET_REF
from shared.db import get_conn


# ---------- P3-1: who is allowed an accuracy, and who is not ----------

# The four columns eval_runs reserves for "this agent was scored against
# labels". Order matters: it lines up with coding_row_params' tuple slice.
ACCURACY_FAMILY = ("accuracy", "f1", "precision", "recall")


@dataclass(frozen=True)
class ScoreableAgent:
    """An agent that has a labeled reference set on the held-out split."""

    agent_name: str
    dataset_refs: tuple[str, ...]   # the sets it may legitimately be scored on
    labels_are: str                 # what the labels ARE, in words


SCOREABLE: dict[str, ScoreableAgent] = {
    "note_structuring": ScoreableAgent(
        agent_name="note_structuring",
        dataset_refs=(ACI_DATASET_REF, PRIMOCK_DATASET_REF),
        labels_are=(
            "clinician-written reference notes. Recall is scored against the "
            "note, precision against the transcript. See score_structuring."),
    ),
}

# agent_name -> why it has no accuracy. The reason is carried into the
# exception, so a caller that trips the guard is told what is missing rather
# than only that it was refused.
UNSCOREABLE: dict[str, str] = {
    "coding": (
        "no held-out set carries gold billing codes. Neither ACI-Bench nor "
        "PriMock57 labels ICD/CPT, so there is nothing to compute precision "
        "or recall of CORRECT codes against. The verified rate says a code "
        "exists in the CMS release, not that it is right for the note, and it "
        "belongs in the metrics JSONB. See ROADMAP P2-4."),
    "care_gap": (
        "the rules are deterministic and unit-tested, which is a correctness "
        "property, not a measured accuracy. There is no labeled set of which "
        "gaps SHOULD have fired on a held-out encounter."),
    "prior_auth": (
        "no held-out encounter carries a labeled prior-auth determination, so "
        "there is no reference to score a PriorAuthOutput against."),
}


class EvalPolicyError(RuntimeError):
    """A write to eval_runs was refused on metric-policy grounds."""


class UnscoreableAgentError(EvalPolicyError):
    """An agent with no labeled set was handed a non-NULL accuracy family."""


class UnknownAgentError(EvalPolicyError):
    """An agent name in neither registry.

    Distinct from UnscoreableAgentError on purpose. A typo must crash as a
    typo, never resolve to "unscoreable" and read like a deliberate policy
    decision about an agent nobody ever registered.
    """


def _require_registered(agent_name: str) -> None:
    """Raise UnknownAgentError unless the name appears in one of the registries."""
    if agent_name in SCOREABLE or agent_name in UNSCOREABLE:
        return
    raise UnknownAgentError(
        f"{agent_name!r} is in neither SCOREABLE nor UNSCOREABLE. Register it "
        f"in governance/evaluate.py before writing eval_runs rows for it. "
        f"Known: {sorted(SCOREABLE) + sorted(UNSCOREABLE)}")


def resolve_scoreable(agent_name: str) -> ScoreableAgent:
    """Return the registry entry, or raise. Use before doing any scoring work.

    Separate from the guard because a caller that intends to WRITE an accuracy
    should fail before it spends anything, not after.
    """
    _require_registered(agent_name)
    if agent_name in UNSCOREABLE:
        raise UnscoreableAgentError(
            f"{agent_name!r} cannot be scored for accuracy: "
            f"{UNSCOREABLE[agent_name]}")
    return SCOREABLE[agent_name]


def assert_accuracy_family_allowed(agent_name: str,
                                   metrics: Mapping[str, float | None]) -> None:
    """The P3-1 invariant, enforced rather than merely documented.

    > No agent outside the scoreable registry may ever be written a non-NULL
    > accuracy, f1, precision or recall.

    Before this existed the rule lived in three prose places nothing checked
    (ROADMAP P2-4, a schema comment, a docstring), and coding_row_params'
    four literal Nones were a convention, not a constraint. A verified rate
    written into `accuracy` would be indistinguishable from a real accuracy on
    a P4-1 chart, which is precisely the failure this project keeps guarding
    against.

    What it does NOT forbid: an unscoreable agent writing rows with the family
    all NULL and its real numbers in the metrics JSONB. P2-4 established that
    shape deliberately, and it stays legal.
    """
    _require_registered(agent_name)
    if agent_name in SCOREABLE:
        return

    claimed = [name for name in ACCURACY_FAMILY
               if metrics.get(name) is not None]
    if claimed:
        raise UnscoreableAgentError(
            f"refusing to write {', '.join(claimed)} for {agent_name!r}: "
            f"{UNSCOREABLE[agent_name]} Put the number in the metrics JSONB "
            f"instead, where nothing will read it as an accuracy.")


def windows_with_counts(agent_name: str, dataset_ref: str,
                        counts: Mapping[str, int]) -> list[str]:
    """Window labels for this agent and dataset whose stored counts match.

    P3-2's guard 2. The counts live in the metrics JSONB under
    provenance.counts, written by the P3-1 ingest path. Compared as a whole
    mapping rather than field by field, because a partial match is not
    evidence of anything.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT window_label, metrics FROM eval_runs "
            "WHERE agent_name = %s AND dataset_ref = %s AND metrics IS NOT NULL",
            (agent_name, dataset_ref),
        ).fetchall()

    wanted = dict(counts)
    return [label for label, metrics in rows
            if (metrics or {}).get("provenance", {}).get("counts") == wanted]


def record_eval_run(*, agent_name: str, model: str, model_effort: str | None,
                    window_label: str, dataset_ref: str, n_examples: int,
                    metrics: Mapping[str, float | None],
                    provenance: Mapping[str, object],
                    measured_at: datetime) -> int:
    """The single guarded writer into eval_runs. Returns the row id.

    measured_at becomes created_at. The column means THE TIME THE MEASUREMENT
    WAS TAKEN, not the time the row was inserted: backfilling July's run today
    under a now() default would stamp it with today's date and P3-3 would read
    the trend backwards. See db/schema.sql.

    The whole metrics mapping also goes into the JSONB alongside provenance,
    so the blob is self-describing and so metrics with no column of their own
    (hallucination_rate, highlights_recall) are not silently dropped.

    accuracy is passed through with .get(), so a None survives as SQL NULL.
    PriMock57 depends on that: placement is not scorable against an
    unsectioned note, and replay() forces the value back to None precisely so
    the 1.0 the arithmetic produces is never published.
    """
    assert_accuracy_family_allowed(agent_name, metrics)

    blob = {**dict(metrics), "provenance": dict(provenance)}

    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO eval_runs (agent_name, model, model_effort, "
            "window_label, dataset_ref, n_examples, accuracy, f1, precision, "
            "recall, metrics, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (agent_name, model, model_effort, window_label, dataset_ref,
             n_examples, metrics.get("accuracy"), metrics.get("f1"),
             metrics.get("precision"), metrics.get("recall"),
             json.dumps(blob), measured_at),
        ).fetchone()
        return row[0]


def score(y_true: list[int], y_pred: list[int]) -> dict:
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, average="binary", zero_division=0)
    return {"accuracy": accuracy_score(y_true, y_pred),
            "precision": precision, "recall": recall, "f1": f1}


def record_run(agent_name: str, model: str, window_label: str,
               dataset_ref: str, y_true: list[int], y_pred: list[int]) -> None:
    m = score(y_true, y_pred)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO eval_runs (agent_name, model, window_label, "
            "dataset_ref, n_examples, accuracy, f1, precision, recall) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (agent_name, model, window_label, dataset_ref, len(y_true),
             m["accuracy"], m["f1"], m["precision"], m["recall"]),
        )


# ---------- P1-4: note-structuring accuracy ----------

@dataclass(frozen=True)
class StructuringCounts:
    """The five raw tallies the structuring metric is computed from.

    Kept as counts rather than as a metrics dict so that a run's artifact can
    be replayed offline and the metrics recomputed from first principles,
    which is what makes the headline number auditable.
    """

    ref_facts: int          # atomic facts in the clinician's note
    captured: int           # of those, how many appear anywhere in the output
    correctly_placed: int   # of those captured, how many in an acceptable section
    gen_facts: int          # atomic facts in the model's note
    supported: int          # of those, how many the transcript actually supports

    def __add__(self, other: "StructuringCounts") -> "StructuringCounts":
        return StructuringCounts(
            ref_facts=self.ref_facts + other.ref_facts,
            captured=self.captured + other.captured,
            correctly_placed=self.correctly_placed + other.correctly_placed,
            gen_facts=self.gen_facts + other.gen_facts,
            supported=self.supported + other.supported,
        )


def score_structuring(c: StructuringCounts) -> dict:
    """Score a free-text SOAP note against a clinician reference note.

    The metric is deliberately asymmetric, and the asymmetry is the first
    thing a reviewer should challenge, so it is stated plainly:

      recall    is scored against the CLINICIAN NOTE. Of the facts the
                clinician wrote, how many did the model capture and file in an
                acceptable SOAP section? The note is the gold standard for
                what matters.

      precision is scored against the TRANSCRIPT, not the note. Of the facts
                the model wrote, how many does the transcript support? The
                clinician note is a selective summary, so a generated fact
                that is in the transcript but absent from the note is a
                legitimate inclusion, not an error. A generated fact supported
                by neither is a hallucination, which is exactly what the P1-2
                structuring prompt forbids. The transcript is the gold
                standard for what is true.

      f1        harmonic mean of the two. This is the headline.

      accuracy  section-placement accuracy: of the reference facts the model
                captured at all, the fraction filed in the right SOAP section.
                This isolates structuring skill from capture skill, and is
                what lands in eval_runs.accuracy.

    Impossible tallies raise. correctly_placed > captured, or captured >
    ref_facts, or supported > gen_facts, all mean a counting bug upstream has
    inflated the score, and a counting bug that returns a plausible number is
    worse than one that crashes.
    """
    if c.captured > c.ref_facts:
        raise ValueError(
            f"captured ({c.captured}) exceeds ref_facts ({c.ref_facts}): "
            f"a counting bug is inflating recall")
    if c.correctly_placed > c.captured:
        raise ValueError(
            f"correctly_placed ({c.correctly_placed}) exceeds captured "
            f"({c.captured}): a fact cannot be placed without being captured")
    if c.supported > c.gen_facts:
        raise ValueError(
            f"supported ({c.supported}) exceeds gen_facts ({c.gen_facts}): "
            f"a counting bug is inflating precision")

    recall = c.correctly_placed / c.ref_facts if c.ref_facts else 0.0
    precision = c.supported / c.gen_facts if c.gen_facts else 0.0
    denom = precision + recall
    f1 = (2 * precision * recall / denom) if denom else 0.0
    placement = c.correctly_placed / c.captured if c.captured else 0.0

    return {
        "accuracy": placement,               # section-placement accuracy
        "precision": precision,              # groundedness in the transcript
        "recall": recall,                    # capture + correct placement
        "f1": f1,                            # the headline
        "hallucination_rate": 1.0 - precision,
    }


# record_structuring_run lived here until P3-1. It was a second, unguarded
# path into eval_runs, and deleting it rather than leaving it deprecated is
# the point: one writer means one place where the accuracy-family invariant is
# enforced. The structuring path now goes through record_eval_run, via
# governance/eval_runner.py::score_artifact.


# ---------- P2-4: the coding routing benchmark ----------

def coding_row_params(*, agent_name: str, model: str, model_effort: str,
                      window_label: str, dataset_ref: str, n_examples: int,
                      metrics: dict) -> tuple:
    """Build the eval_runs row for one coding-benchmark arm.

    accuracy/f1/precision/recall are NULL by construction: no held-out set
    carries gold codes, so the verified rate lives only in the metrics JSONB
    (spec §6). Pure and DB-free, so the NULL contract is unit-testable.
    """
    return (agent_name, model, model_effort, window_label, dataset_ref,
            n_examples, None, None, None, None, json.dumps(metrics))


def record_coding_run(*, agent_name: str, model: str, model_effort: str,
                      window_label: str, dataset_ref: str, n_examples: int,
                      metrics: dict) -> int:
    """Write one coding-benchmark arm to eval_runs. Returns the row id."""
    params = coding_row_params(
        agent_name=agent_name, model=model, model_effort=model_effort,
        window_label=window_label, dataset_ref=dataset_ref,
        n_examples=n_examples, metrics=metrics)
    # The guard runs on the row actually about to be written, not on the
    # metrics dict that produced it. coding_row_params hardcodes the four
    # NULLs today; this is what notices if that ever stops being true.
    assert_accuracy_family_allowed(
        agent_name, dict(zip(ACCURACY_FAMILY, params[6:10])))
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO eval_runs (agent_name, model, model_effort, "
            "window_label, dataset_ref, n_examples, accuracy, f1, precision, "
            "recall, metrics) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            params,
        ).fetchone()
        return row[0]
