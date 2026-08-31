"""P3-1: the accuracy-family invariant, enforced rather than merely written down.

> No agent outside the scoreable registry may ever be written a non-NULL
> accuracy, f1, precision or recall.

Before P3-1 that rule existed in three prose places nothing checked, and
`coding_row_params`' four literal Nones were a convention rather than a
constraint. The failure it guards is silent and expensive: a verified rate
written into `accuracy` is indistinguishable from a real accuracy on a chart,
and this project's whole discipline is that a number means what its column
says it means.

Needs no dataset and no database. The guard is pure policy.
"""
from __future__ import annotations

import pytest

from governance.evaluate import (
    ACCURACY_FAMILY,
    SCOREABLE,
    UNSCOREABLE,
    UnknownAgentError,
    UnscoreableAgentError,
    assert_accuracy_family_allowed,
    coding_row_params,
    resolve_scoreable,
)

NULL_FAMILY = {name: None for name in ACCURACY_FAMILY}


# ---------- the registries themselves ----------

def test_note_structuring_is_the_scoreable_agent_and_says_what_its_labels_are():
    agent = resolve_scoreable("note_structuring")

    assert agent.agent_name == "note_structuring"
    assert agent.dataset_refs, "a scoreable agent must name the sets it scores on"
    # The registry exists to answer "which labeled set?" out loud. An empty
    # answer would let the gate be satisfied by an agent nobody can point at a
    # reference for, which is the exact failure the ROADMAP warns about:
    # "before claiming an accuracy number for any agent, name the labeled set".
    assert agent.labels_are.strip()


def test_the_two_registries_are_disjoint():
    """An agent in both would make the guard's answer depend on which dict is
    consulted first, which is a coin flip deciding whether a number is legal."""
    assert not set(SCOREABLE) & set(UNSCOREABLE)


@pytest.mark.parametrize("agent_name", sorted(UNSCOREABLE))
def test_every_unscoreable_agent_states_why(agent_name):
    assert UNSCOREABLE[agent_name].strip()


# ---------- the guard refuses ----------

@pytest.mark.parametrize("agent_name", sorted(UNSCOREABLE))
def test_an_unscoreable_agent_cannot_be_written_an_accuracy(agent_name):
    with pytest.raises(UnscoreableAgentError) as exc:
        assert_accuracy_family_allowed(
            agent_name, {**NULL_FAMILY, "accuracy": 0.97})

    # The refusal carries the registry's reason, so a caller learns WHAT is
    # missing rather than only that it was told no.
    assert UNSCOREABLE[agent_name][:40] in str(exc.value)


@pytest.mark.parametrize("metric", ACCURACY_FAMILY)
def test_each_of_the_four_columns_trips_the_guard_on_its_own(metric):
    """Not just accuracy. A verified rate parked in `f1` or `precision` is the
    same lie in a different column, and a guard that only watched one of them
    would be a guard in name only."""
    with pytest.raises(UnscoreableAgentError):
        assert_accuracy_family_allowed("coding", {**NULL_FAMILY, metric: 0.5})


def test_the_refusal_names_every_column_that_was_claimed():
    with pytest.raises(UnscoreableAgentError) as exc:
        assert_accuracy_family_allowed(
            "coding", {**NULL_FAMILY, "f1": 0.9, "recall": 0.8})

    message = str(exc.value)
    assert "f1" in message and "recall" in message


# ---------- the guard permits ----------

def test_the_p2_4_coding_shape_stays_legal():
    """The invariant is not "unscoreable agents get no rows". Coding writes
    real rows with the family NULL and its verified rate in the metrics JSONB,
    and P2-4 chose that shape deliberately."""
    assert_accuracy_family_allowed(
        "coding", {**NULL_FAMILY, "verified_rate": 96.65, "unchecked": 37.05})


def test_the_existing_coding_row_builder_still_passes_the_guard():
    """A regression test on the P2-4 path itself, not on a hand-built dict.
    If coding_row_params ever stops hardcoding its four NULLs, this fails."""
    params = coding_row_params(
        agent_name="coding", model="claude-opus-4-8", model_effort="high",
        window_label="v1", dataset_ref="aci-bench-heldout-v1",
        n_examples=113, metrics={"verified_rate": 97.35})

    # positions 6..9 are accuracy, f1, precision, recall
    assert_accuracy_family_allowed("coding",
                                   dict(zip(ACCURACY_FAMILY, params[6:10])))


def test_the_coding_writer_actually_calls_the_guard(monkeypatch):
    """Mutation-checked, in the spirit of P2-6's reducer test.

    Every other test here exercises the guard directly, so all of them would
    still pass if record_coding_run had simply forgotten to call it. This
    breaks coding_row_params the way a future regression would, by having it
    emit a verified rate in the f1 slot, and asserts the writer refuses.

    That it raises UnscoreableAgentError rather than a connection error is the
    second half of the claim: the guard runs BEFORE get_conn, so a refused
    write never reaches the database at all.
    """
    from governance import evaluate

    def leaky(**kwargs):
        return ("coding", "m", "high", "w", "d", 113, None, 0.9735, None, None,
                "{}")

    monkeypatch.setattr(evaluate, "coding_row_params", leaky)

    with pytest.raises(UnscoreableAgentError, match="f1"):
        evaluate.record_coding_run(
            agent_name="coding", model="m", model_effort="high",
            window_label="w", dataset_ref="d", n_examples=113, metrics={})


def test_a_scoreable_agent_may_write_the_whole_family():
    assert_accuracy_family_allowed(
        "note_structuring",
        {"accuracy": 0.88, "f1": 0.87, "precision": 0.97, "recall": 0.79})


# ---------- a typo is a typo, not a policy decision ----------

def test_an_unregistered_agent_raises_unknown_not_unscoreable():
    """The distinction is the point. If a typo resolved to "unscoreable" it
    would read like a deliberate decision about an agent nobody registered,
    and the real agent's rows would silently stop being written."""
    with pytest.raises(UnknownAgentError):
        assert_accuracy_family_allowed("note_structurin", NULL_FAMILY)


def test_an_unregistered_agent_is_unknown_even_with_an_all_null_family():
    """The all-NULL shape is the one that would otherwise sail through: there
    is nothing to refuse, so an unknown name would be waved past and its rows
    would land under a name no reader can resolve."""
    with pytest.raises(UnknownAgentError):
        assert_accuracy_family_allowed("brand_new_agent", NULL_FAMILY)


def test_resolve_scoreable_refuses_an_unscoreable_agent_by_name():
    with pytest.raises(UnscoreableAgentError, match="gold billing codes"):
        resolve_scoreable("coding")


def test_resolve_scoreable_distinguishes_unknown_from_unscoreable():
    with pytest.raises(UnknownAgentError):
        resolve_scoreable("codingg")
