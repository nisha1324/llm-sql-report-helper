"""Offline backend: turn a question into SQL with a small semantic layer, no LLM.

It reads the question as metric + optional dimensions + filters + ranking,
the way a BI tool's search box does, and builds the SQL from the definitions
in config/semantic_layer.yml. In order:

1. Decline requests for personal data, writes and data the business doesn't
   hold (the same three kinds the evaluation set's must-decline cases test).
2. Abstain on question shapes it can't express (period comparisons, absence,
   thresholds, cohorts, ratios, hierarchy). A plain metric-by-dimension query
   that dropped the condition would give a confident wrong number.
3. Find the dimensions (only after a grouping word: "by", "each", "top 5"...),
   then the metric in what's left, then year and value filters and a ranking.
4. Pick the first grain of the metric where every expression's table aliases
   can be joined, and write one SELECT.

The result is an Answer: SQL to run, a decline with its kind, or an abstain
with the reason. The guard in db.py still checks whatever SQL comes out.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .policy import ROOT, Policy, load_policy
from .schema import describe_tables

LAYER_PATH = ROOT / "config" / "semantic_layer.yml"
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
YEAR = re.compile(r"\b(20\d\d)\b")
ALIAS = re.compile(r"\b([a-z]{1,2})\.[A-Z]")
MASK = "§"


@dataclass(frozen=True)
class Answer:
    sql: str | None = None
    declined: str | None = None   # privacy | write | no_data
    reason: str = ""              # why it declined or abstained, or a note on the SQL


def normalise(question: str) -> str:
    text = question.lower().replace("’", "'")
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"[^a-z0-9%'\- ]+", " ", text)
    return " ".join(str(NUMBER_WORDS.get(w, w)) for w in text.split())


def _word_re(phrase: str) -> str:
    """A phrase (or a regex fragment) as whole words, with an optional plural."""
    return rf"\b(?:{phrase})(?:s|es)?\b"


def _camel_words(name: str) -> str:
    return " ".join(re.findall(r"[A-Z][a-z]*", name)).lower()


class OfflineBackend:
    name = "offline"

    def __init__(self, layer_path: Path = LAYER_PATH, policy: Policy | None = None, db_path: Path | None = None):
        self.layer = yaml.safe_load(layer_path.read_text())
        policy = policy or load_policy()
        tables = describe_tables(policy, **({"db_path": db_path} if db_path else {}),
                                 list_values=frozenset(self.layer["filter_values"]))
        self.values = {}  # "Table.Column" -> list of values
        for t in tables:
            for c in t["columns"]:
                if "values" in c:
                    self.values[f"{t['table']}.{c['name']}"] = c["values"]
        dec = self.layer["decline"]
        # Restricted columns in the policy, as words: "PostalCode" -> "postal code".
        restricted = {_camel_words(col) for t in tables for col in t["restricted"]}
        self.privacy = sorted(restricted | set(dec["privacy"]))
        self.write_verbs = set(dec["write_verbs"])
        self.no_data = dec["no_data"]

    # 1. decline -----------------------------------------------------------
    def _decline(self, text: str) -> Answer | None:
        words = text.split()
        if words and (words[0] in self.write_verbs or (words[0] == "please" and len(words) > 1
                                                      and words[1] in self.write_verbs)):
            return Answer(declined="write", reason="this helper only reads data; changes go through the system owner")
        for w in self.privacy:
            if re.search(_word_re(re.escape(w)), text):
                return Answer(declined="privacy", reason=f"'{w}' is personal data and isn't available to reports")
        for w in self.no_data:
            if re.search(_word_re(re.escape(w)), text):
                return Answer(declined="no_data", reason=f"the database has no {w} data")
        return None

    # 2. abstain on shapes the layer can't express ------------------------
    def _unsupported(self, text: str) -> str | None:
        for shape, words in self.layer["unsupported"].items():
            for w in words:
                if re.search(_word_re(re.escape(w)), text):
                    return f"needs {shape} ('{w}'), which the offline path can't express"
        return None

    # 3. read the question -------------------------------------------------
    def _find_dimensions(self, text: str) -> tuple[list[str], int | None, str]:
        """Dimensions in the order found, the N of a "top N"/"3 artists" ranking, and the
        text with matched words masked so the metric search can't reuse them."""
        groups = "|".join(re.escape(w) for w in self.layer["group_words"]) + r"|[1-9]\d?"
        found, n = [], None
        candidates = sorted(((w, d) for d, spec in self.layer["dimensions"].items() for w in spec["words"]),
                            key=lambda wd: -len(wd[0]))  # longest phrase first: "sales rep" before "rep"
        for word, dim in candidates:
            pattern = rf"\b(?P<g>{groups})\s+(?P<mid>(?:[a-z0-9'\-]+\s+){{0,2}}?){_word_re(re.escape(word))}"
            for m in re.finditer(pattern, text):
                mid = m.group("mid").split()
                if "all" in mid:
                    continue
                num = m.group("g") if m.group("g").isdigit() else next((w for w in mid if w.isdigit()), None)
                if num and int(num) < 100:
                    n = n or int(num)
                if dim not in found:
                    found.append(dim)
                start = m.start() + len(m.group("g")) + 1 + len(m.group("mid"))
                text = text[:start] + MASK * (m.end() - start) + text[m.end():]
                break
        for word, dim in candidates:  # "monthly" groups on its own
            if word.endswith("ly") and re.search(_word_re(word), text):
                if dim not in found:
                    found.append(dim)
                text = re.sub(_word_re(word), MASK, text)
        # An album already carries its artist.
        if "album" in found and "artist" in found:
            found.remove("artist")
        return found, n, text

    def _find_metric(self, text: str) -> str | None:
        for metric, spec in self.layer["metrics"].items():
            if any(re.search(_word_re(w), text) for w in spec["words"]):
                return metric
        return None

    def _filters(self, text: str) -> tuple[list[tuple[str, str]], list[str]]:
        """Value filters as (Table.Column, value), and the years named in the question."""
        found = []
        for column, values in self.values.items():
            for v in values:
                if re.search(rf"\b{re.escape(v.lower())}\b", text):
                    found.append((column, v))
        return found, sorted(set(YEAR.findall(text)))

    # 4. write SQL ---------------------------------------------------------
    def _select_for(self, dim: str, grain: str) -> list[list[str]]:
        spec = self.layer["dimensions"][dim]
        return (spec.get("select_by_grain") or {}).get(grain, spec["select"])

    def _filter_expr(self, column: str, grain: str) -> str:
        exprs = self.layer["filter_values"][column]["grain_expr"]
        return exprs.get(grain, exprs["default"])

    def _joins(self, exprs: list[str], grain: str) -> list[str] | None:
        """JOIN clauses that bring in every alias the expressions use, or None if this grain can't."""
        have = set(self.layer["grains"][grain]["provides"])
        joins: list[str] = []

        def add(alias: str, depth: int = 0) -> bool:
            if alias in have:
                return True
            sql = self.layer["joins"].get(alias)
            if sql is None or depth > len(self.layer["joins"]):
                return False
            if not all(add(dep, depth + 1) for dep in dict.fromkeys(ALIAS.findall(sql)) if dep != alias):
                return False
            joins.append(sql)
            have.add(alias)
            return True

        aliases = dict.fromkeys(a for e in exprs for a in ALIAS.findall(e))
        return joins if all(add(a) for a in aliases) else None

    def answer(self, question: str) -> Answer:
        text = normalise(question)
        if (declined := self._decline(text)) is not None:
            return declined
        if (why := self._unsupported(text)) is not None:
            return Answer(reason=why)

        dims, n, rest = self._find_dimensions(text)
        metric = self._find_metric(rest)
        if metric is None:
            metric = next((self.layer["dimensions"][d].get("default_metric") for d in dims
                           if self.layer["dimensions"][d].get("default_metric")), None)
        if metric is None:
            return Answer(reason="no metric found (revenue, orders, customers, units...)")
        values, years = self._filters(text)
        if len(years) > 1 and "year" not in dims:
            return Answer(reason=f"names {len(years)} years but doesn't ask by year")
        share = any(re.search(_word_re(w), text) for w in self.layer["share_words"])
        if share and (dims or not values):
            return Answer(reason="a share needs exactly one named value and no grouping")
        ranked = any(re.search(_word_re(w), text) for w in self.layer["ranking"]["descending"])
        if ranked and not dims:
            return Answer(reason="asks for a ranking but names nothing to rank")

        spec = self.layer["metrics"][metric]
        for grain, m_expr in spec["expr"].items():
            selects = [s for d in dims for s in self._select_for(d, grain)]
            filters = [(self._filter_expr(col, grain), v) for col, v in values]
            year_expr = "strftime('%Y', i.InvoiceDate)"
            exprs = [m_expr, *(e for e, _ in selects), *(e for e, _ in filters),
                     *([year_expr] if years else []),
                     *(self.layer["dimensions"][d].get("group", "") for d in dims)]
            joins = self._joins(exprs, grain)
            if joins is not None:
                break
        else:
            return Answer(reason=f"no join path for {metric} with {', '.join(dims) or 'these filters'}")

        rnd = spec.get("round")
        where = []
        if len(years) == 1:
            where.append(f"{year_expr} = '{years[0]}'")
        elif years:
            where.append(f"{year_expr} BETWEEN '{years[0]}' AND '{years[-1]}'")
        conditions = [f"{e} = '{v}'" for e, v in filters]
        if share:
            cond = " AND ".join(conditions)
            value = f"ROUND(100.0 * SUM(CASE WHEN {cond} THEN {m_expr.removeprefix('SUM(')[:-1]} ELSE 0 END) " \
                    f"/ {m_expr}, 1) AS {metric}_share_pct" if m_expr.startswith("SUM(") else None
            if value is None:
                return Answer(reason=f"a share of {metric} isn't defined")
        else:
            where += conditions
            value = f"ROUND({m_expr}, {rnd}) AS {metric}" if rnd is not None else f"{m_expr} AS {metric}"

        lines = ["SELECT " + ", ".join([*(f"{e} AS {a}" for e, a in selects), value]),
                 "FROM " + " ".join([self.layer["grains"][grain]["from"], *joins])]
        if where:
            lines.append("WHERE " + " AND ".join(where))
        if dims:
            keys = [self.layer["dimensions"][d].get("group") or ", ".join(e for e, _ in self._select_for(d, grain))
                    for d in dims]
            lines.append("GROUP BY " + ", ".join(keys))
            time_first = self.layer["dimensions"][dims[0]].get("time")
            if ranked:
                lines.append(f"ORDER BY {metric} DESC LIMIT {n or 1}")
            elif time_first:
                lines.append("ORDER BY " + ", ".join(a for _, a in self._select_for(dims[0], grain)))
            else:
                lines.append(f"ORDER BY {metric} DESC")
        note = ("share of " if share else "") + metric + (f" by {', '.join(dims)}" if dims else "") \
            + f" at {grain} level"
        return Answer(sql="\n".join(lines), reason=note)
