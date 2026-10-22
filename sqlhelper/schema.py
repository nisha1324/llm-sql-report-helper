"""Build the schema card: the compact description of the database an LLM sees.

It lists only what the policy allows (restricted columns are named but not
described), adds keys, row counts and the values of the lookup columns named
in config/business_notes.yml, then the business definitions from that file.
"""
import sqlite3
from pathlib import Path

import yaml

from .db import connect
from .policy import DB_PATH, ROOT, Policy, load_policy

NOTES_PATH = ROOT / "config" / "business_notes.yml"


def _table_names(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name")]


def describe_tables(policy: Policy, db_path: Path = DB_PATH, list_values: frozenset[str] = frozenset()) -> list[dict]:
    """One dict per allowed table: columns, keys, row count and, for columns
    named in `list_values` ("Table.Column"), their distinct values."""
    con = connect(db_path)
    out = []
    for table in _table_names(con):
        if not policy.table_allowed(table):
            continue
        info = con.execute(f'PRAGMA table_info("{table}")').fetchall()
        fks = {r[3]: f"{r[2]}.{r[4]}" for r in con.execute(f'PRAGMA foreign_key_list("{table}")')}
        columns, restricted = [], []
        for _cid, name, ctype, _notnull, _default, pk in info:
            if not policy.column_allowed(table, name):
                restricted.append(name)
                continue
            col = {"name": name, "type": ctype.split("(")[0], "pk": bool(pk), "fk": fks.get(name)}
            if f"{table}.{name}" in list_values:
                col["values"] = [r[0] for r in con.execute(
                    f'SELECT DISTINCT "{name}" FROM "{table}" WHERE "{name}" IS NOT NULL ORDER BY 1')]
            columns.append(col)
        rows = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        out.append({"table": table, "rows": rows, "columns": columns, "restricted": restricted})
    con.close()
    return out


def render_card(tables: list[dict], notes: dict) -> str:
    lines = ["# Schema card: Chinook music store (SQLite)", "", notes["about"].strip(), "", "## Tables", ""]
    for t in tables:
        lines.append(f"### {t['table']} ({t['rows']:,} rows)")
        for c in t["columns"]:
            tags = []
            if c["pk"]:
                tags.append("primary key")
            if c["fk"]:
                tags.append(f"-> {c['fk']}")
            line = f"- {c['name']} {c['type']}" + (f" ({', '.join(tags)})" if tags else "")
            if "values" in c:
                line += ": " + ", ".join(repr(v) for v in c["values"])
            lines.append(line)
        if t["restricted"]:
            lines.append(f"- Not available (personal data): {', '.join(t['restricted'])}")
        lines.append("")
    lines += ["## Metric definitions", ""]
    lines += [f"- **{k}**: {v}" for k, v in notes["metrics"].items()]
    lines += ["", "## Things to know", ""]
    lines += [f"- {g}" for g in notes["gotchas"]]
    lines += ["", "## Rules", "",
              "- One read-only SELECT (or WITH ... SELECT) per answer; no recursive CTEs.",
              "- Name the columns you need; SELECT * fails on tables with personal data.",
              ""]
    return "\n".join(lines)


def build_card(policy: Policy | None = None, db_path: Path = DB_PATH, notes_path: Path = NOTES_PATH) -> str:
    policy = policy or load_policy()
    notes = yaml.safe_load(notes_path.read_text())
    list_values = frozenset(notes.get("list_values", []))
    tables = describe_tables(policy, db_path, list_values)
    known = {f"{t['table']}.{c['name']}" for t in tables for c in t["columns"]}
    if unknown := sorted(list_values - known):
        raise ValueError(f"list_values names columns that aren't in the card: {unknown}")
    return render_card(tables, notes)
