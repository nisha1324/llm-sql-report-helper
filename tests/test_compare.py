import pytest

from sqlhelper.compare import compare

GOLD = [("USA", 523.06), ("Canada", 303.96), ("France", 195.1)]


def test_identical_result_matches():
    assert compare(GOLD, GOLD, ordered=True).detail == "exact"


def test_column_names_and_order_do_not_matter():
    swapped = [(r, c) for c, r in GOLD]
    assert compare(GOLD, swapped, ordered=True).match


def test_extra_columns_are_allowed():
    pred = [(c, 13, r) for c, r in GOLD]
    v = compare(GOLD, pred, ordered=True)
    assert v.match and v.detail == "extra columns"


def test_missing_column_fails():
    assert not compare(GOLD, [(c,) for c, _ in GOLD]).match


def test_row_order_counts_only_when_ordered():
    shuffled = [GOLD[2], GOLD[0], GOLD[1]]
    assert compare(GOLD, shuffled, ordered=False).match
    v = compare(GOLD, shuffled, ordered=True)
    assert not v.match and v.detail == "row order differs"


def test_numbers_within_tolerance():
    assert compare([(5.8,)], [(5.80487,)]).match           # unrounded average
    assert compare([(59,)], [(59.0,)]).match               # int vs float
    assert not compare([(5.8,)], [(5.83,)]).match
    assert compare([(7.1,)], [(7.12,)], tolerance=0.05).match
    assert not compare([(22.5,)], [(0.225,)], tolerance=0.05).match  # fraction is not a percentage


def test_text_and_nulls():
    assert compare([("Rock ",)], [("Rock",)]).match
    assert not compare([("rock",)], [("Rock",)]).match
    assert compare([("Andrew Adams", None)], [("Andrew Adams", None)]).match
    assert not compare([("Andrew Adams", None)], [("Andrew Adams", "")]).match
    assert not compare([("5",)], [(5,)]).match


def test_row_count_must_match():
    v = compare(GOLD, GOLD[:2])
    assert not v.match and "2 rows" in v.detail
    assert compare([], []).match


def test_columns_must_line_up_row_by_row():
    # Each column alone has the right values, but they're paired wrongly.
    gold = [("a", 1), ("b", 2)]
    assert not compare(gold, [("a", 2), ("b", 1)]).match


def test_duplicate_rows_are_counted():
    assert not compare([("x",), ("x",), ("y",)], [("x",), ("y",), ("y",)]).match


def test_identical_columns_in_prediction():
    # Two identical predicted columns: either can be used.
    assert compare([(1, "a"), (2, "b")], [(1, 1, "a"), (2, 2, "b")]).match


@pytest.mark.parametrize("n_extra", [0, 10, 25])
def test_wide_predictions_stay_fast(n_extra):
    gold = [(i, f"n{i}", i * 1.5) for i in range(50)]
    pred = [tuple([i] + [0] * n_extra + [f"n{i}", i * 1.5]) for i in range(50)]
    assert compare(gold, pred).match


def test_two_gold_columns_cannot_share_one_predicted_column():
    assert not compare([(1, 1), (2, 2)], [(1, "x"), (2, "y")]).match


def test_each_predicted_row_is_used_once():
    # Columns hold the same values, and every gold row exists in the
    # prediction, but (a, 1) appears twice in gold and once in the prediction.
    gold = [("a", 1), ("a", 1), ("b", 2), ("b", 2)]
    assert not compare(gold, [("a", 1), ("a", 2), ("b", 1), ("b", 2)]).match
