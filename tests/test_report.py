import random

import pytest

from sqlhelper.db import QueryResult, run_query
from sqlhelper.policy import DB_PATH
from sqlhelper.questions import load_questions
from sqlhelper.report import check_numbers, humanise, template_report, unit, write_template

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="run scripts/download_data.py first")


def res(columns, rows, truncated=False):
    return QueryResult(list(columns), [tuple(r) for r in rows], truncated)


BY_COUNTRY = res(["country", "revenue"], [("USA", 523.06), ("Canada", 303.96), ("France", 195.10)])
BY_YEAR = res(["year", "revenue"], [("2009", 449.46), ("2010", 481.45), ("2011", 469.58)])


def unsupported(text, result, question=""):
    return check_numbers(text, result, question).unsupported


# ---------------------------------------------------------------- the checker

def test_cells_pass_as_written_or_rounded():
    assert unsupported("USA brought in $523.06, Canada $303.96 and France $195.1.", BY_COUNTRY) == []
    assert unsupported("USA brought in about $523 and Canada $304.", BY_COUNTRY) == []


def test_invented_and_mistyped_numbers_are_caught():
    assert unsupported("USA brought in $532.06.", BY_COUNTRY) == ["$532.06"]   # transposed digits
    assert unsupported("USA brought in $523.07.", BY_COUNTRY) == ["$523.07"]   # one cent off
    assert unsupported("USA brought in $600.", BY_COUNTRY) == ["$600"]
    assert unsupported("USA is 40.0% of the total.", BY_COUNTRY) == ["40.0%"]


def test_rounding_must_be_right_for_the_decimals_shown():
    assert unsupported("$524", BY_COUNTRY) == ["$524"]       # 523.06 rounds to 523
    assert unsupported("$523.1", BY_COUNTRY) == []
    assert unsupported("$523.0", BY_COUNTRY) == ["$523.0"]


def test_derived_values():
    text = ("Three countries, together $1,022.12, an average of $340.71. USA is 51.2% of that, "
            "$219.10 ahead of Canada, which is 41.9% below USA.")
    assert unsupported(text, BY_COUNTRY) == []
    trend = "Revenue rose from $449.46 to $469.58 (+4.5%), up $31.99 in 2010 alone (+7.1%)."
    assert unsupported(trend, BY_YEAR) == []


def test_sign_is_checked_only_when_written():
    yoy = res(["year", "yoy_pct"], [("2010", 7.1), ("2011", -2.5)])
    assert unsupported("2011 fell 2.5%.", yoy) == []
    assert unsupported("2011: -2.5%.", yoy) == []
    assert unsupported("2011: +2.5%.", yoy) == ["+2.5%"]
    assert unsupported("2010: −7.1%.", yoy) == ["−7.1%"]  # Unicode minus counts as a sign


def test_percent_and_plain_numbers_are_separate_pools():
    # 22.5 is a share (%) here, not a plain number in the result
    r = res(["country", "customers"], [("USA", 9), ("Other", 31)])
    assert unsupported("USA has 22.5% of customers.", r) == []
    assert unsupported("USA has 22.5 customers.", r) == ["22.5"]


def test_dates_labels_and_the_question():
    by_month = res(["month", "revenue"], [("2010-01", 52.62), ("2010-02", 46.62)])
    assert unsupported("Best month 2010-01 with $52.62.", by_month) == []
    assert unsupported("Best month 2010-03.", by_month) == ["2010-03"]
    assert unsupported("Revenue in 2010 was $481.45.", BY_YEAR) == []          # a year label is text in a cell
    assert unsupported("The top 3 in 2013.", BY_COUNTRY, "Top 3 countries in 2013?") == []
    assert unsupported("The top 4.", BY_COUNTRY) == ["4"]


def test_numbers_inside_names_are_not_numbers():
    r = res(["artist", "revenue"], [("U2", 105.93), ("Iron Maiden", 138.60)])
    check = check_numbers("U2 earned $105.93; the MP3 version sold well.", r)
    assert [n.text for n in check.numbers] == ["$105.93"] and check.ok


def test_shorthand_is_refused_rather_than_guessed():
    # each bare number is in the result (a cell, the row count); the suffix changes its meaning
    assert unsupported("USA brought in $523.06k.", BY_COUNTRY) == ["$523.06k"]
    assert unsupported("USA is 3x France.", BY_COUNTRY) == ["3x"]
    assert unsupported("France is the 3rd market.", BY_COUNTRY) == ["3rd"]


def test_row_count_and_sources():
    check = check_numbers("3 countries; USA has $523.06.", BY_COUNTRY)
    assert [n.source for n in check.numbers] == ["row count", "cell in column revenue"]


# ------------------------------------------------------------ template writer

def test_humanise_and_unit():
    assert humanise("avg_order_value") == "average order value"
    assert humanise("yoy_pct") == "year-on-year change"
    assert humanise("usa_share_pct") == "USA share"
    assert humanise("FirstName") == "first name"
    assert unit("revenue_per_customer", [1.5]) == "money"
    assert unit("yoy_pct", [1.5]) == "pct"
    assert unit("customers", [3]) == "count"
    assert unit("tracks_per_order", [5.44]) == "decimal"


def test_template_shapes():
    assert write_template("", res(["revenue"], [])) == "No rows matched the question."
    assert write_template("", res(["revenue"], [(2328.6,)])) == "Revenue: $2,328.60."
    assert write_template("", res(["year"], [("2010",)])) == "Answer: 2010."
    assert write_template("", res(["rep", "customers"], [("Jane Peacock", 21)])) == "Jane Peacock: customers 21."
    assert write_template("", BY_YEAR) == (
        "Revenue by year, 2009 to 2011: $449.46 in 2009 and $469.58 in 2011 (+4.5%). "
        "Highest in 2010 ($481.45), lowest in 2009 ($449.46). Total $1,400.49 over 3 years.")
    assert write_template("", BY_COUNTRY) == (
        "Revenue by country (3 countries): highest USA with $523.06 (51.2% of the total shown); "
        "then Canada with $303.96; lowest France with $195.10. These 3 rows add up to $1,022.12.")
    listed = res(["Country"], [(c,) for c in "ABCDEFG"])
    assert write_template("", listed) == "7 rows: A, B, C, D, E and the rest in the table."


def test_averages_are_not_added_up():
    r = res(["country", "revenue_per_customer"], [("USA", 40.24), ("Canada", 37.99)])
    text = write_template("", r)
    assert "add up" not in text and "of the total" not in text


def test_truncated_results_say_so():
    r = res(["n"], [(i,) for i in range(500)], truncated=True)
    assert "Only the first 500 rows came back" in write_template("", res(["k", "n"], [(str(i), i) for i in range(500)],
                                                                         truncated=True))
    assert template_report("", r).check.ok


def _random_result(rng: random.Random) -> QueryResult:
    n = rng.choice([0, 1, 2, 3, 7, 40])
    shape = rng.choice(["metric", "label+metric", "time+metric", "labels", "label+2metrics"])
    name = rng.choice(["revenue", "orders", "share_pct", "tracks_per_order", "avg_spend"])

    def value():
        if name == "orders":
            return rng.randint(0, 5000)
        v = round(rng.uniform(-50, 5000), rng.choice([1, 2]))
        return v if rng.random() > 0.1 else None

    labels = [f"{rng.choice('ABCDE')}{k}x" for k in range(n)]
    times = [f"{2000 + k}" if rng.random() < 0.5 else f"2010-{k + 1:02d}" for k in range(n)]
    times = sorted(set(times))[:n]
    if shape == "metric":
        return res([name], [(value(),) for _ in range(min(n, 1))])
    if shape == "labels":
        return res(["name", "manager"], [(l, rng.choice([None, "Boss"])) for l in labels])
    if shape == "time+metric":
        return res(["year", name], [(t, value()) for t in times])
    if shape == "label+2metrics":
        return res(["item", name, "units"], [(l, value(), rng.randint(1, 9)) for l in labels])
    return res(["item", name], [(l, value()) for l in labels])


def test_template_always_passes_its_own_check():
    rng = random.Random(7)
    for _ in range(400):
        r = _random_result(rng)
        rep = template_report("", r)
        assert rep.check.ok, (r, rep.text, rep.check.unsupported)


@needs_db
def test_every_gold_answer_gets_a_checked_report():
    answerable, _ = load_questions()
    for q in answerable:
        rep = template_report(q.question, run_query(q.sql))
        assert rep.check.ok and rep.check.numbers or rep.text.startswith(("Answer:", "15 rows")), (q.id, rep.text)
