from governance.evaluate import score


def test_perfect_prediction_scores_one():
    m = score([1, 0, 1, 0], [1, 0, 1, 0])
    assert m["f1"] == 1.0 and m["accuracy"] == 1.0


def test_metrics_are_bounded():
    m = score([1, 1, 0, 0], [1, 0, 0, 1])
    assert 0.0 <= m["precision"] <= 1.0
    assert 0.0 <= m["recall"] <= 1.0


# ---------- P2-4: the coding row's all-NULL accuracy contract ----------

from governance.evaluate import coding_row_params  # noqa: E402


def test_coding_row_leaves_the_accuracy_family_null():
    # The whole point: a coding row must not write a verified rate into a column
    # a Phase 3 dashboard reads as accuracy. All four stay NULL; metrics carries
    # the real numbers (spec §6).
    params = coding_row_params(
        agent_name="coding", model="claude-sonnet-5", model_effort="xhigh",
        window_label="v1", dataset_ref="aci-bench-heldout-v1",
        n_examples=118, metrics={"verified_rate": 94.0})
    # (agent_name, model, model_effort, window_label, dataset_ref, n_examples,
    #  accuracy, f1, precision, recall, metrics_json)
    assert params[6] is None and params[7] is None
    assert params[8] is None and params[9] is None
    assert params[2] == "xhigh"
    import json
    assert json.loads(params[10])["verified_rate"] == 94.0


def test_coding_row_carries_n_examples_as_the_intersection():
    params = coding_row_params(
        agent_name="coding", model="m", model_effort="high", window_label="v1",
        dataset_ref="d", n_examples=110, metrics={})
    assert params[5] == 110
