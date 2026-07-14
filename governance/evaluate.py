"""Re-score an agent against a held-out labeled set and store the result.

The held-out set is leak-free and never used to tune rules or prompts.
This is the same discipline used in ClinAIQA: measured numbers only.

Two scoring paths live here, and metric arithmetic lives *only* here so it
cannot drift between callers:

  score()             binary classification, for the Phase 2 and 3 agents
  score_structuring() free-text SOAP notes, for the P1-4 headline metric
"""
from __future__ import annotations

from dataclasses import dataclass

from sklearn.metrics import precision_recall_fscore_support, accuracy_score

from shared.db import get_conn


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


def record_structuring_run(*, agent_name: str, model: str, window_label: str,
                           dataset_ref: str, n_examples: int,
                           metrics: dict) -> int:
    """Write one structuring eval to eval_runs. Returns the row id.

    accuracy is nullable: the PriMock57 held-out notes are free-text GP
    shorthand rather than SOAP sections, so placement cannot be honestly
    scored there and the column is left NULL rather than filled with a
    number that does not mean what the column says it means.
    """
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO eval_runs (agent_name, model, window_label, "
            "dataset_ref, n_examples, accuracy, f1, precision, recall) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (agent_name, model, window_label, dataset_ref, n_examples,
             metrics.get("accuracy"), metrics["f1"],
             metrics["precision"], metrics["recall"]),
        ).fetchone()
        return row[0]
