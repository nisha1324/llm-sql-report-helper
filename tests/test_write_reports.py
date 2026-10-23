from scripts.write_reports import figures, plant, render, small_whole, typos
from sqlhelper.db import QueryResult
from sqlhelper.report import number_tokens, write_template

BY_MONTH = QueryResult(["month", "revenue"], [("2010-01", 52.62), ("2010-02", 46.62), ("2010-03", 44.62)])


def tok(text):
    return next(number_tokens(text))


def test_number_spans_point_into_the_original_text():
    text = "Best 2010-01 with $52.62, then +4.5%."
    assert [text[m.start():m.end()] for m in number_tokens(text)] == ["$52.62", "+4.5%"]


def test_render_keeps_the_format():
    assert render(tok("$1,956.24"), 2152.0) == "$2,152.00"
    assert render(tok("+0.2%"), -0.3) == "-0.3%"
    assert render(tok("13"), 14.3) == "14"
    assert render(tok("5.44"), 5.0) == "5.00"


def test_typos():
    assert ("typo: neighbouring digits swapped", "$532.06") in typos(tok("$523.06"))
    assert {t for k, t in typos(tok("$523.06")) if "last digit" in k} == {"$523.05", "$523.07"}
    # digits either side of a comma aren't neighbours, so no "5,119"
    assert {t for _, t in typos(tok("1,519"))} == {"1,159", "1,591", "1,518"}
    assert small_whole("13") and small_whole("+5") and not small_whole("$5.80") and not small_whole("1,519")


def test_figures_leave_labels_alone():
    text = write_template("Monthly revenue for 2010?", BY_MONTH)
    assert all(not n.source.startswith(("date", "the question")) for _, n in figures(text, BY_MONTH, ""))
    assert "2010-01" in text and len(figures(text, BY_MONTH, "")) >= 4


def test_planted_errors():
    text = write_template("", BY_MONTH)
    cases = plant(text, BY_MONTH, "")
    scaled = [c for c in cases if c["kind"].startswith("scaled")]
    swaps = [c for c in cases if c["kind"] == "two figures swapped"]
    money = [c for c in scaled if c["original"].startswith("$")]
    assert money and all(c["caught"] for c in money)
    # the row count "3" scaled to "2" or "4" may match a small value by chance; that's what the report measures
    assert swaps and not any(c["caught"] for c in swaps)  # the check can't see a swap: both values are real
    assert all(c["text"] != text for c in cases)
