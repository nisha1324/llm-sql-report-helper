"""Load and validate config/policy.yml."""
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "policy.yml"
DB_PATH = ROOT / "data" / "chinook.sqlite"


@dataclass(frozen=True)
class Policy:
    tables: frozenset[str]
    restricted: dict[str, frozenset[str]]  # table -> restricted columns (lower-case)
    max_sql_chars: int
    max_rows: int
    timeout_seconds: float

    def table_allowed(self, table: str) -> bool:
        return table.lower() in self.tables

    def column_allowed(self, table: str, column: str) -> bool:
        return column.lower() not in self.restricted.get(table.lower(), frozenset())


def load_policy(path: Path = POLICY_PATH) -> Policy:
    raw = yaml.safe_load(path.read_text())
    tables = frozenset(t.lower() for t in raw["tables"])
    restricted = {}
    for table, cols in (raw.get("restricted_columns") or {}).items():
        if table.lower() not in tables:
            raise ValueError(f"restricted_columns names a table that isn't allowed: {table}")
        restricted[table.lower()] = frozenset(c.lower() for c in cols)
    lim = raw["limits"]
    return Policy(tables, restricted, int(lim["max_sql_chars"]), int(lim["max_rows"]),
                  float(lim["timeout_seconds"]))
