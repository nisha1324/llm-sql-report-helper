import dataclasses
import hashlib
import sqlite3

import pytest

from sqlhelper import db
from sqlhelper.db import QueryRejected, run_query
from sqlhelper.policy import load_policy


def run(sql, fixture_db, policy):
    return run_query(sql, policy, fixture_db)


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM Customer",
    "select c.FirstName, sum(i.Total) from Customer c join Invoice i using (CustomerId) group by 1",
    "WITH spend AS (SELECT CustomerId, SUM(Total) s FROM Invoice GROUP BY 1) SELECT MAX(s) FROM spend",
    "SELECT CustomerId, RANK() OVER (ORDER BY Total DESC) FROM Invoice",
    "SELECT * FROM (SELECT Country FROM Customer) t",
    "/* report */ -- monthly\n  SELECT 1;",
    "SELECT COUNT(*) FROM Customer WHERE Country LIKE 'U%'",
])
def test_allowed_queries_run(sql, fixture_db, policy):
    run(sql, fixture_db, policy)


@pytest.mark.parametrize("sql, reason", [
    ("", "empty"),
    ("DELETE FROM Customer", "only SELECT"),
    ("UPDATE Invoice SET Total = 0", "only SELECT"),
    ("INSERT INTO Track VALUES (5000)", "only SELECT"),
    ("DROP TABLE Customer", "only SELECT"),
    ("PRAGMA table_info(Customer)", "only SELECT"),
    ("ATTACH DATABASE '/tmp/x.db' AS x", "only SELECT"),
    ("VACUUM INTO '/tmp/copy.db'", "only SELECT"),
    ("SELECT 1; DROP TABLE Customer", "only one statement per query"),
    ("SELECT Value FROM Secret", "table Secret"),
    ("SELECT name FROM sqlite_master", "table sqlite_master"),
    ("SELECT Email FROM Customer", "Customer.Email"),
    ("SELECT * FROM Customer", "Customer.Email"),
    ("SELECT c.Email AS e FROM Customer c", "Customer.Email"),
    ("SELECT COUNT(*) FROM Customer WHERE Email LIKE '%example%'", "Customer.Email"),
    ("SELECT FirstName FROM Customer ORDER BY Email", "Customer.Email"),
    ("WITH x AS (SELECT Email FROM Customer) SELECT COUNT(*) FROM x", "Customer.Email"),
    ("SELECT (SELECT MAX(Email) FROM Customer)", "Customer.Email"),
    ("SELECT load_extension('evil')", "load_extension"),
    ("SELECT random()", "random"),
    ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n", "recursive"),
    ("WITH n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n", "recursive"),
    ("SELECT FirstName FROM Customer WHERE " + " OR ".join(["1=1"] * 200), "longer than"),
    ("SELECT nonsense FROM Customer", "SQL error"),
])
def test_rejected_queries_give_a_reason(sql, reason, fixture_db, policy):
    with pytest.raises(QueryRejected) as e:
        run(sql, fixture_db, policy)
    assert reason in e.value.reason


def test_engine_blocks_writes_even_if_the_shape_check_is_bypassed(fixture_db, policy, monkeypatch):
    monkeypatch.setattr(db, "check_shape", lambda sql, _policy: sql)
    for sql in ("DELETE FROM Customer", "WITH x AS (SELECT 1) DELETE FROM Customer", "CREATE TABLE t (x)"):
        with pytest.raises(QueryRejected, match="only SELECT"):
            run(sql, fixture_db, policy)


def test_connection_is_read_only_without_the_authorizer(fixture_db):
    con = db.connect(fixture_db)
    with pytest.raises(sqlite3.OperationalError, match="readonly|query_only"):
        con.execute("DELETE FROM Customer")


def test_database_file_is_unchanged(fixture_db, policy):
    before = hashlib.sha256(fixture_db.read_bytes()).hexdigest()
    for sql in ("DELETE FROM Customer", "SELECT COUNT(*) FROM Invoice", "VACUUM"):
        try:
            run(sql, fixture_db, policy)
        except QueryRejected:
            pass
    assert hashlib.sha256(fixture_db.read_bytes()).hexdigest() == before


def test_row_cap_flags_truncation(fixture_db, policy):
    res = run("SELECT TrackId FROM Track ORDER BY TrackId", fixture_db, policy)
    assert len(res.rows) == policy.max_rows and res.truncated
    res = run("SELECT TrackId FROM Track WHERE TrackId <= 100", fixture_db, policy)
    assert len(res.rows) == 100 and not res.truncated


def test_slow_query_hits_the_time_limit(fixture_db, policy):
    quick = dataclasses.replace(policy, timeout_seconds=0.2)
    with pytest.raises(QueryRejected, match="longer than 0.2 s"):
        run("SELECT COUNT(*) FROM Track a, Track b, Track c", fixture_db, quick)


def test_results_are_correct(fixture_db, policy):
    res = run("SELECT Country, SUM(Total) AS revenue FROM Customer JOIN Invoice USING (CustomerId) "
              "GROUP BY Country ORDER BY revenue DESC", fixture_db, policy)
    assert res.columns == ["Country", "revenue"]
    assert res.rows == [("USA", 11.0), ("Brazil", 10.0)]


def test_policy_rejects_restricting_an_unknown_table(tmp_path):
    path = tmp_path / "policy.yml"
    path.write_text("tables: [Customer]\nrestricted_columns:\n  Employee: [Email]\n"
                    "limits: {max_sql_chars: 10, max_rows: 1, timeout_seconds: 1}\n")
    with pytest.raises(ValueError, match="Employee"):
        load_policy(path)


def test_shipped_policy_loads():
    p = load_policy()
    assert p.table_allowed("Invoice") and not p.column_allowed("Customer", "email")
