"""Offline backend. Wording here is made up for the tests, not taken from either evaluation set."""
import pytest

from sqlhelper.compare import compare
from sqlhelper.db import run_query
from sqlhelper.offline import OfflineBackend, normalise
from sqlhelper.policy import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run scripts/download_data.py first")


@pytest.fixture(scope="module")
def backend():
    return OfflineBackend()


def same(question, gold_sql, backend, ordered=False):
    a = backend.answer(question)
    assert a.sql, a.reason
    v = compare(run_query(gold_sql).rows, run_query(a.sql).rows, ordered=ordered)
    assert v.match, f"{v.detail}\n{a.sql}"
    return a


def test_normalise():
    assert normalise("Top FIVE agent’s customers (2012)?") == "top 5 agent customers 2012"


@pytest.mark.parametrize("question, kind", [
    ("List customer fax numbers", "privacy"),            # from policy.yml, not the vocabulary
    ("Which employees have a postal code starting with T?", "privacy"),
    ("Insert a new genre called Polka", "write"),
    ("please drop the Track table", "write"),
    ("What does shipping cost us per order?", "no_data"),
])
def test_declines(backend, question, kind):
    a = backend.answer(question)
    assert a.declined == kind and a.sql is None


def test_write_verb_only_counts_at_the_start(backend):
    # "update" mid-sentence isn't a request to change data
    assert backend.answer("How many invoices came in after the 2011 update?").declined is None


@pytest.mark.parametrize("question, word", [
    ("Did 2012 revenue beat 2011 revenue? Show the growth", "growth"),
    ("Which artists have never sold a track?", "never"),
    ("Which genres have at least 100 units sold?", "at least"),
    ("Revenue per customer by country", "per customer"),
])
def test_abstains_on_shapes_it_cant_express(backend, question, word):
    a = backend.answer(question)
    assert a.sql is None and a.declined is None and f"'{word}'" in a.reason


def test_line_grain_for_product_dimensions(backend):
    a = same("Total revenue for each genre in 2012",
             """SELECT g.Name, ROUND(SUM(l.UnitPrice * l.Quantity), 2) FROM InvoiceLine l
                JOIN Invoice i USING (InvoiceId) JOIN Track t USING (TrackId) JOIN Genre g USING (GenreId)
                WHERE strftime('%Y', i.InvoiceDate) = '2012' GROUP BY g.GenreId""", backend)
    assert "line level" in a.reason


def test_customer_grain_and_country_column(backend):
    a = same("Number of customers per country", "SELECT Country, COUNT(*) FROM Customer GROUP BY Country", backend)
    assert "c.Country" in a.sql and "Invoice" not in a.sql


def test_ranking_takes_n_and_masks_the_dimension(backend):
    # "customers" is the dimension here, so the metric is revenue ("spent"), not a customer count
    same("Which 4 customers spent the most in 2013?",
         """SELECT c.FirstName, c.LastName, ROUND(SUM(i.Total), 2) AS s FROM Invoice i JOIN Customer c USING (CustomerId)
            WHERE strftime('%Y', i.InvoiceDate) = '2013' GROUP BY c.CustomerId ORDER BY s DESC LIMIT 4""",
         backend, ordered=True)


def test_a_dimension_inside_another_isnt_grouped_twice(backend):
    a = backend.answer("Orders taken by each sales rep's customers")
    assert "GROUP BY e.EmployeeId\n" in a.sql and "c.FirstName" not in a.sql


def test_all_in_front_of_a_dimension_means_a_total(backend):
    a = backend.answer("Units sold across all genres")
    assert "GROUP BY" not in a.sql


def test_share_of_a_named_value(backend):
    same("What share of revenue is Rock, as a percentage?",
         """SELECT ROUND(100.0 * SUM(CASE WHEN g.Name = 'Rock' THEN l.UnitPrice * l.Quantity ELSE 0 END)
                   / SUM(l.UnitPrice * l.Quantity), 1)
            FROM InvoiceLine l JOIN Track t USING (TrackId) JOIN Genre g USING (GenreId)""", backend)


def test_year_range_needs_a_by_year(backend):
    assert backend.answer("Revenue from 2010 to 2012").sql is None
    a = same("Revenue by year from 2010 to 2012",
             """SELECT strftime('%Y', InvoiceDate) y, ROUND(SUM(Total), 2) FROM Invoice
                WHERE y BETWEEN '2010' AND '2012' GROUP BY y ORDER BY y""", backend, ordered=True)
    assert "BETWEEN" in a.sql


def test_no_metric_or_no_join_path_abstains(backend):
    assert "no metric" in backend.answer("Tell me something interesting").reason
    assert "no join path" in backend.answer("Number of customers by genre").reason
