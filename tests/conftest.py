import sqlite3

import pytest

from sqlhelper.policy import Policy


@pytest.fixture
def fixture_db(tmp_path):
    """Tiny database with one restricted column and one table outside the policy."""
    path = tmp_path / "shop.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, FirstName TEXT, Country TEXT, Email TEXT);
        CREATE TABLE Invoice (InvoiceId INTEGER PRIMARY KEY, CustomerId INTEGER, Total NUMERIC);
        CREATE TABLE Track (TrackId INTEGER PRIMARY KEY);
        CREATE TABLE Secret (Value TEXT);
        INSERT INTO Customer VALUES (1, 'Ana', 'Brazil', 'ana@example.com'),
                                    (2, 'Ben', 'USA', 'ben@example.com'),
                                    (3, 'Cy', 'USA', 'cy@example.com');
        INSERT INTO Invoice VALUES (1, 1, 10.0), (2, 2, 5.5), (3, 2, 4.5), (4, 3, 1.0);
        INSERT INTO Secret VALUES ('s3cret');
    """)
    con.executemany("INSERT INTO Track VALUES (?)", [(i,) for i in range(1, 1001)])
    con.commit()
    con.close()
    return path


@pytest.fixture
def policy():
    return Policy(
        tables=frozenset({"customer", "invoice", "track"}),
        restricted={"customer": frozenset({"email"})},
        max_sql_chars=500,
        max_rows=100,
        timeout_seconds=5,
    )
