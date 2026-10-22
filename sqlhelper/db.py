"""Run generated SQL safely against the Chinook database.

Four layers, so no single check has to be perfect:
1. Shape check: one statement, starts with SELECT or WITH, length cap.
2. The file is opened read-only (`mode=ro`) with `PRAGMA query_only`.
3. An SQLite authorizer allows only SELECT, reads of allowed tables and
   non-restricted columns and an allow-list of functions (no recursive CTEs).
   SQLite calls it while compiling the statement, so aliases, CTEs,
   subqueries and comments can't hide a table or column from it.
4. A time limit (progress handler) and a cap on returned rows.
"""
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .policy import DB_PATH, Policy, load_policy

# Functions a reporting query needs. Anything else (load_extension, random, ...) is denied.
ALLOWED_FUNCTIONS = frozenset("""
count sum avg min max total group_concat string_agg
round abs coalesce ifnull nullif iif like glob
lower upper length substr substring trim ltrim rtrim replace instr printf format
date time datetime julianday strftime unixepoch
cast typeof
row_number rank dense_rank ntile lag lead first_value last_value nth_value percent_rank cume_dist
sqrt power pow floor ceil ceiling ln log log10 exp mod
""".split())

_LEADING_COMMENTS = re.compile(r"^\s*(?:(?:--[^\n]*\n)|(?:/\*.*?\*/)|\s)*", re.DOTALL)


class QueryRejected(Exception):
    """The SQL broke a rule. `reason` is safe to show to a user or an LLM."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False
    elapsed_ms: float = 0.0
    sql: str = field(default="", repr=False)


def check_shape(sql: str, policy: Policy) -> str:
    """Cheap checks before SQLite sees the text. Returns the SQL without a trailing ';'."""
    if not sql or not sql.strip():
        raise QueryRejected("empty query")
    if len(sql) > policy.max_sql_chars:
        raise QueryRejected(f"query longer than {policy.max_sql_chars} characters")
    body = sql.strip().rstrip(";").rstrip()
    if not sqlite3.complete_statement(body + ";"):
        raise QueryRejected("query is not a complete SQL statement")
    head = _LEADING_COMMENTS.sub("", body, count=1)
    first = re.match(r"[A-Za-z]+", head)
    if not first or first.group(0).upper() not in ("SELECT", "WITH"):
        raise QueryRejected("only SELECT queries are allowed")
    return body


def _authorizer(policy: Policy, denied: list[str], real_tables: set[str]):
    def check(action, arg1, arg2, _db, _source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = arg1 or "", arg2 or ""
            if table.lower() not in real_tables:
                # Missing tables fail earlier ("no such table"), so a read of a
                # name that isn't a table is a recursive CTE reading itself.
                denied.append("recursive queries are not allowed")
                return sqlite3.SQLITE_DENY
            if not policy.table_allowed(table):
                denied.append(f"table {table} is not available")
                return sqlite3.SQLITE_DENY
            if column and not policy.column_allowed(table, column):
                denied.append(f"column {table}.{column} is restricted (personal data)")
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            if (arg2 or "").lower() in ALLOWED_FUNCTIONS:
                return sqlite3.SQLITE_OK
            denied.append(f"function {arg2}() is not allowed")
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_RECURSIVE:  # reports don't need them; easiest runaway query
            denied.append("recursive queries are not allowed")
            return sqlite3.SQLITE_DENY
        denied.append("only SELECT queries are allowed")
        return sqlite3.SQLITE_DENY
    return check


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} not found. Run: python scripts/download_data.py")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    return con


def run_query(sql: str, policy: Policy | None = None, db_path: Path = DB_PATH) -> QueryResult:
    """Run one guarded SELECT. Raises QueryRejected with a readable reason."""
    policy = policy or load_policy()
    body = check_shape(sql, policy)
    con = connect(db_path)
    real_tables = {r[0].lower() for r in con.execute("SELECT name FROM sqlite_schema")}
    real_tables |= {"sqlite_schema", "sqlite_master"}
    denied: list[str] = []
    con.set_authorizer(_authorizer(policy, denied, real_tables))
    deadline = time.monotonic() + policy.timeout_seconds
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    start = time.perf_counter()
    try:
        cur = con.execute(body)
        rows = cur.fetchmany(policy.max_rows + 1)
        columns = [d[0] for d in cur.description or []]
    except sqlite3.ProgrammingError as e:  # a DatabaseError subclass, so it goes first
        if "one statement" in str(e):
            raise QueryRejected("only one statement per query") from e
        raise QueryRejected(f"SQL error: {e}") from e
    except sqlite3.DatabaseError as e:
        if denied:
            raise QueryRejected(denied[0]) from e
        if "interrupted" in str(e):
            raise QueryRejected(f"query took longer than {policy.timeout_seconds:g} s") from e
        raise QueryRejected(f"SQL error: {e}") from e
    finally:
        elapsed = (time.perf_counter() - start) * 1000
        con.close()
    truncated = len(rows) > policy.max_rows
    return QueryResult(columns, rows[: policy.max_rows], truncated, elapsed, body)
