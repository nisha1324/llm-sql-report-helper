import pytest

from sqlhelper.backend import Answer
from sqlhelper.evaluate import evaluate, summarise
from sqlhelper.policy import DB_PATH
from sqlhelper.questions import DeclineCase, Question

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run scripts/download_data.py first")

GOLD = "SELECT COUNT(*) FROM Invoice"


class Stub:
    """Answers from a fixed table: question -> Answer, or an exception to raise."""
    name = "stub"

    def __init__(self, table):
        self.table = table

    def answer(self, question):
        a = self.table[question]
        if isinstance(a, Exception):
            raise a
        return a


def q(text, sql=GOLD, ordered=False):
    return Question(text, text, "kpi", "easy", sql, ordered)


def test_every_outcome():
    stub = Stub({
        "right": Answer(sql="SELECT COUNT(InvoiceId) AS n FROM Invoice"),
        "wrong": Answer(sql="SELECT COUNT(*) FROM Customer"),
        "blocked": Answer(sql="SELECT Email FROM Customer"),
        "broken sql": Answer(sql="SELECT nope FROM Invoice"),
        "unsure": Answer(reason="no metric"),
        "refused": Answer(declined="privacy", reason="personal"),
        "crash": RuntimeError("boom"),
        "emails?": Answer(declined="privacy"),
        "margin?": Answer(reason="?"),
        "phones?": Answer(sql="SELECT Phone FROM Customer"),
        "count?": Answer(sql="SELECT COUNT(*) FROM Invoice"),
    })
    answerable = [q(t) for t in ("right", "wrong", "blocked", "broken sql", "unsure", "refused", "crash")]
    decline = [DeclineCase(t, t, "privacy") for t in ("emails?", "margin?", "phones?", "count?")]
    out = {o.id: o for o in evaluate(stub, {"dev": (answerable, decline)})}
    assert [out[t].status for t in ("right", "wrong", "blocked", "broken sql", "unsure", "refused", "crash")] == \
        ["correct", "wrong", "rejected", "rejected", "abstained", "declined", "error"]
    assert "restricted" in out["blocked"].detail and "no such column" in out["broken sql"].detail
    assert out["crash"].detail == "RuntimeError: boom" and out["refused"].declined_as == "privacy"
    assert [out[t].status for t in ("emails?", "margin?", "phones?", "count?")] == \
        ["declined", "abstained", "rejected", "answered"]
    assert out["count?"].detail == "returned 1 row(s)" and out["emails?"].must_decline


def test_row_order_counts_only_when_ordered():
    years = "SELECT strftime('%Y', InvoiceDate) y, COUNT(*) FROM Invoice GROUP BY y"
    stub = Stub({"by year": Answer(sql=years + " ORDER BY y DESC"), "ranked": Answer(sql=years + " ORDER BY y DESC")})
    out = evaluate(stub, {"dev": ([q("by year", years + " ORDER BY y"), q("ranked", years + " ORDER BY y", True)], [])})
    assert [o.status for o in out] == ["correct", "wrong"] and out[1].detail == "row order differs"


def test_summary():
    stub = Stub({"a": Answer(sql=GOLD), "b": Answer(sql="SELECT 1"), "c": Answer(reason="?"), "d": Answer(sql=GOLD)})
    s = summarise(evaluate(stub, {"dev": ([q(t) for t in "abcd"], [])}))
    assert s.questions == 4 and s.counts == {"correct": 2, "wrong": 1, "abstained": 1}
    assert s.answered == 3 and s.accuracy == 0.5 and s.precision == pytest.approx(2 / 3)
    assert summarise([]).precision is None
