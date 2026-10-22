import pytest
import yaml

from sqlhelper.db import run_query
from sqlhelper.policy import DB_PATH, load_policy
from sqlhelper.schema import build_card, describe_tables, render_card

needs_chinook = pytest.mark.skipif(not DB_PATH.exists(), reason="run scripts/download_data.py first")


def notes_file(tmp_path, list_values):
    path = tmp_path / "notes.yml"
    path.write_text(yaml.safe_dump({"about": "Test shop.", "metrics": {"revenue": "SUM(Total)"},
                                    "gotchas": ["none"], "list_values": list_values}))
    return path


def test_card_hides_restricted_columns_and_other_tables(fixture_db, policy, tmp_path):
    card = build_card(policy, fixture_db, notes_file(tmp_path, ["Customer.Country"]))
    assert "Secret" not in card
    assert "- Email" not in card
    assert "Not available (personal data): Email" in card
    assert "Country TEXT: 'Brazil', 'USA'" in card


def test_card_rejects_unknown_or_restricted_list_values(fixture_db, policy, tmp_path):
    with pytest.raises(ValueError, match="Customer.Email"):
        build_card(policy, fixture_db, notes_file(tmp_path, ["Customer.Email"]))


def test_render_marks_keys():
    card = render_card([{"table": "T", "rows": 2, "restricted": [], "columns": [
        {"name": "Id", "type": "INTEGER", "pk": True, "fk": None},
        {"name": "UId", "type": "INTEGER", "pk": False, "fk": "U.Id"}]}],
        {"about": "x", "metrics": {}, "gotchas": []})
    assert "- Id INTEGER (primary key)" in card and "- UId INTEGER (-> U.Id)" in card


@needs_chinook
@pytest.mark.parametrize("sql, expected", [
    # Facts stated in config/business_notes.yml
    ("SELECT ROUND(SUM(Total), 2) FROM Invoice", 2328.6),
    ("SELECT ROUND(SUM(UnitPrice * Quantity), 2) FROM InvoiceLine", 2328.6),
    ("SELECT COUNT(*) FROM InvoiceLine WHERE Quantity <> 1", 0),
    ("SELECT MIN(InvoiceDate) || ' ' || MAX(InvoiceDate) FROM Invoice", "2009-01-01 00:00:00 2013-12-22 00:00:00"),
    ("SELECT COUNT(*) FROM Invoice i JOIN Customer c USING (CustomerId) WHERE i.BillingCountry <> c.Country", 0),
    ("SELECT COUNT(*) FROM Track t LEFT JOIN InvoiceLine l USING (TrackId) WHERE l.TrackId IS NULL", 1519),
    ("SELECT COUNT(*) FROM Track WHERE Composer IS NULL", 978),
    ("SELECT COUNT(*) FROM Customer WHERE Company IS NULL", 49),
    ("SELECT COUNT(*) FROM Customer WHERE State IS NULL", 29),
    ("SELECT GROUP_CONCAT(DISTINCT SupportRepId) FROM (SELECT SupportRepId FROM Customer ORDER BY 1)", "3,4,5"),
    ("SELECT COUNT(*) FROM (SELECT Name FROM Playlist GROUP BY Name HAVING COUNT(*) > 1)", 4),
    ("SELECT GROUP_CONCAT(DISTINCT UnitPrice) FROM (SELECT UnitPrice FROM Track ORDER BY 1)", "0.99,1.99"),
])
def test_business_notes_facts_hold(sql, expected):
    assert run_query(sql).rows == [(expected,)]


@needs_chinook
def test_real_card_lists_every_allowed_table():
    tables = describe_tables(load_policy())
    assert len(tables) == 11
    card = build_card()
    assert "Email" in card.split("Not available")[1]  # only named as unavailable
    assert card.count("- Email") == 0
