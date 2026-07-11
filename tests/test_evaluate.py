from governance.evaluate import score


def test_perfect_prediction_scores_one():
    m = score([1, 0, 1, 0], [1, 0, 1, 0])
    assert m["f1"] == 1.0 and m["accuracy"] == 1.0


def test_metrics_are_bounded():
    m = score([1, 1, 0, 0], [1, 0, 0, 1])
    assert 0.0 <= m["precision"] <= 1.0
    assert 0.0 <= m["recall"] <= 1.0
