import pytest

from scripts.ask import ask
from sqlhelper.backend import Answer
from sqlhelper.offline import OfflineBackend
from sqlhelper.policy import DB_PATH

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="run scripts/download_data.py first")


class Scripted:
    name = "scripted"

    def __init__(self, answer: Answer):
        self._answer = answer

    def answer(self, question: str) -> Answer:
        return self._answer


class LyingWriter:
    """A writer whose text has an invented number, like a careless LLM."""

    def write(self, question, result):
        from sqlhelper.report import Report, check_numbers
        text = "Revenue was $9,999.99."
        return Report(text, "liar", check_numbers(text, result, question))


@needs_db
def test_offline_question_end_to_end():
    out = ask("Show revenue by year.", OfflineBackend())
    assert "SELECT" in out and "|   2010 |    481.45 |" in out
    assert "Summary (template writer; all 13 numbers traced to the result):" in out
    assert "Total $2,328.60 over 5 years." in out


def test_declines_and_abstains_show_the_reason():
    assert ask("?", Scripted(Answer(declined="privacy", reason="emails are personal"))) == \
        "Declined (privacy): emails are personal"
    assert ask("?", Scripted(Answer(reason="which year?"))) == "No answer: which year?"


@needs_db
def test_guard_rejection_is_shown_not_run():
    out = ask("?", Scripted(Answer(sql="SELECT Email FROM Customer")))
    assert out.endswith("Blocked by the guard: column Customer.Email is restricted (personal data)")


@needs_db
def test_long_results_are_cut_in_the_table():
    out = ask("?", Scripted(Answer(sql="SELECT Name FROM Genre ORDER BY Name")))
    assert "(first 20 of 25 rows)" in out and "25 rows:" in out


@needs_db
def test_check_result_is_reported_for_any_writer():
    out = ask("?", Scripted(Answer(sql="SELECT ROUND(SUM(Total), 2) AS revenue FROM Invoice")), LyingWriter())
    assert "Summary (liar writer; WARNING, numbers not in the result: $9,999.99):" in out
