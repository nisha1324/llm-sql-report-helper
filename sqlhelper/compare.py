"""Decide whether a generated query answered the question: compare its result
with the gold result (execution accuracy, as in text-to-SQL benchmarks).

Rules:
- Column names don't matter; columns are matched by their values.
- Extra columns are fine (an answer may add a helpful count); missing ones aren't.
- Same number of rows. Row order only counts when the question asks for a ranking.
- Numbers match within a tolerance (default 0.01, i.e. a cent); 5 and 5.0 are equal.
- Text matches exactly after trimming spaces; NULL only matches NULL.
"""
from dataclasses import dataclass
from itertools import product

DEFAULT_TOLERANCE = 0.01


@dataclass(frozen=True)
class Verdict:
    match: bool
    detail: str  # "exact", "extra columns", or why it failed


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def values_equal(a, b, tol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if _is_num(a) and _is_num(b):
        return abs(a - b) <= tol + 1e-9
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    return False


def _sort_key(v):
    # NULLs first, then numbers, then text; numbers and text never compare directly.
    if v is None:
        return (0, 0, "")
    if _is_num(v):
        return (1, float(v), "")
    return (2, 0, str(v).strip())


def _columns_equal(gold_col: list, pred_col: list, tol: float) -> bool:
    """Same values in any order (a necessary condition for a column match)."""
    gold_col, pred_col = sorted(gold_col, key=_sort_key), sorted(pred_col, key=_sort_key)
    return all(values_equal(g, p, tol) for g, p in zip(gold_col, pred_col))


def _rows_match(gold_rows: list[tuple], pred_rows: list[tuple], tol: float, ordered: bool) -> bool:
    if ordered:
        return all(all(values_equal(g, p, tol) for g, p in zip(gr, pr)) for gr, pr in zip(gold_rows, pred_rows))
    # Pair each gold row with an unused predicted row. Sorting both first keeps
    # equal rows next to each other, so the greedy pass rarely needs to look far.
    remaining = sorted(pred_rows, key=lambda r: [_sort_key(v) for v in r])
    for gr in sorted(gold_rows, key=lambda r: [_sort_key(v) for v in r]):
        for i, pr in enumerate(remaining):
            if all(values_equal(g, p, tol) for g, p in zip(gr, pr)):
                del remaining[i]
                break
        else:
            return False
    return True


def compare(gold_rows: list[tuple], pred_rows: list[tuple], *, ordered: bool = False,
            tolerance: float = DEFAULT_TOLERANCE) -> Verdict:
    gold_rows, pred_rows = [tuple(r) for r in gold_rows], [tuple(r) for r in pred_rows]
    if len(gold_rows) != len(pred_rows):
        return Verdict(False, f"{len(pred_rows)} rows, expected {len(gold_rows)}")
    if not gold_rows:
        return Verdict(True, "exact")
    n_gold, n_pred = len(gold_rows[0]), len(pred_rows[0])
    if n_pred < n_gold:
        return Verdict(False, f"{n_pred} columns, expected at least {n_gold}")

    gold_cols = list(zip(*gold_rows))
    pred_cols = list(zip(*pred_rows))
    # For each gold column, the predicted columns holding the same values.
    candidates = [[j for j in range(n_pred) if _columns_equal(list(g), list(pred_cols[j]), tolerance)]
                  for g in gold_cols]
    if not all(candidates):
        missing = next(i for i, c in enumerate(candidates) if not c)
        return Verdict(False, f"no column matches gold column {missing + 1}")

    # Try each way of assigning gold columns to distinct candidate columns
    # (usually exactly one: only ties like two identical columns give more).
    for mapping in product(*candidates):
        if len(set(mapping)) < n_gold:
            continue
        projected = [tuple(r[j] for j in mapping) for r in pred_rows]
        if _rows_match(gold_rows, projected, tolerance, ordered):
            return Verdict(True, "exact" if n_pred == n_gold else "extra columns")
    return Verdict(False, "row order differs" if ordered and compare(
        gold_rows, pred_rows, ordered=False, tolerance=tolerance).match else "values differ")
