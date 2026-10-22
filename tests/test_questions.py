import pytest
import yaml

from sqlhelper.compare import compare
from sqlhelper.db import QueryRejected, run_query
from sqlhelper.policy import DB_PATH
from sqlhelper.questions import load_questions

needs_chinook = pytest.mark.skipif(not DB_PATH.exists(), reason="run scripts/download_data.py first")


def write(tmp_path, data):
    path = tmp_path / "q.yml"
    path.write_text(yaml.safe_dump(data))
    return path


GOOD = {"id": "q1", "question": "How many?", "area": "kpi", "level": "easy", "sql": "SELECT 1"}


def test_loads_defaults(tmp_path):
    (q,), decline = load_questions(write(tmp_path, {"answerable": [GOOD]}))
    assert not q.ordered and q.tolerance == 0.01 and q.published is None and decline == []


@pytest.mark.parametrize("bad, message", [
    ([GOOD, GOOD], "duplicate"),
    ([{**GOOD, "area": "finance"}], "area"),
    ([{**GOOD, "sql": ""}], "sql"),
    ([{**GOOD, "orderd": True}], "unknown fields"),
])
def test_rejects_bad_answerable(tmp_path, bad, message):
    with pytest.raises(ValueError, match=message):
        load_questions(write(tmp_path, {"answerable": bad}))


def test_privacy_and_write_cases_need_naive_sql(tmp_path):
    with pytest.raises(ValueError, match="naive_sql"):
        load_questions(write(tmp_path, {"must_decline": [{"id": "d1", "question": "emails?", "kind": "privacy"}]}))
    _, (d,) = load_questions(write(tmp_path, {"must_decline": [{"id": "d1", "question": "margin?", "kind": "no_data"}]}))
    assert d.naive_sql is None


def test_ids_are_unique_across_sections(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        load_questions(write(tmp_path, {"answerable": [GOOD],
                                        "must_decline": [{"id": "q1", "question": "x", "kind": "no_data"}]}))


def test_real_set_loads():
    answerable, decline = load_questions()
    assert len(answerable) == 32 and len(decline) == 7


@needs_chinook
def test_every_gold_query_passes_the_guard_and_matches_published_figures():
    answerable, _ = load_questions()
    for q in answerable:
        res = run_query(q.sql)
        assert res.rows and not res.truncated, q.id
        if q.published is not None:
            assert compare(q.published, res.rows, ordered=q.ordered, tolerance=q.tolerance).match, q.id


@needs_chinook
def test_guard_rejects_every_naive_privacy_or_write_query():
    _, decline = load_questions()
    for d in decline:
        if d.naive_sql:
            with pytest.raises(QueryRejected):
                run_query(d.naive_sql)
