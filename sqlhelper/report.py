"""Turn a query result into a short business summary, and check its numbers.

Two parts:
- `write_template(question, result)`: an offline writer. It describes the
  result by its shape (one figure, a trend over time, a breakdown, a list),
  using only values from the rows and a few simple derivations.
- `check_numbers(text, result, question)`: the rule every writer, template or
  LLM, must pass. Each number in the text has to be traceable to the result:

    a cell (also numbers inside text cells, such as a year label), the row count,
    a column's total, average or number of values, a change between two rows (adjacent rows,
    first and last, highest and lowest, next to each other by size) as a
    difference or a % change, a row's share of the column total, or a number
    the question itself used ("top 3", "in 2013").

  A number written with fewer decimals matches if it rounds correctly
  ("$2,329" for 2,328.60). A number with an explicit sign (+7.1%, -2.5%) must
  match with that sign; an unsigned one is compared by size, so "fell 2.5%"
  is fine. Only magnitudes are checked: a real number attached to the wrong
  label passes, which is why the result table is always shown with the text.
"""
import re
from dataclasses import dataclass, field

from .db import QueryResult

TIME_LABEL = re.compile(r"^\d{4}(-\d{2}){0,2}$")
DATE_TOKEN = re.compile(r"(?<![\w.])\d{4}-\d{2}(?:-\d{2})?(?!\w|\.\d)")
# A number, with an optional sign, $ and %. Letters straight after it ("1.2k",
# "3x", "2nd") are captured so the checker can refuse them instead of reading "1.2".
NUMBER_TOKEN = re.compile(
    r"(?<![\w.,])([+\-−]?)\$?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(%?)([A-Za-z]*)")
WORDS = {"avg": "average", "aov": "average order value", "yoy": "year-on-year change", "pct": "%",
         "usa": "USA", "uk": "UK"}
MONEY_WORDS = ("revenue", "spend", "total", "sales", "value", "amount", "price")
PCT_WORDS = ("pct", "percent", "share", "rate")
NOT_ADDITIVE = ("avg", "average", "per", "mean")


# ---------------------------------------------------------------- the checker

@dataclass(frozen=True)
class Number:
    text: str            # as written, e.g. "$2,328.60" or "+0.2%"
    value: float
    decimals: int
    signed: bool         # written with an explicit + or -
    percent: bool
    source: str | None   # where it came from, or None if it couldn't be traced


@dataclass(frozen=True)
class NumberCheck:
    numbers: list[Number]

    @property
    def ok(self) -> bool:
        return all(n.source for n in self.numbers)

    @property
    def unsupported(self) -> list[str]:
        return [n.text for n in self.numbers if not n.source]


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _numbers_in(text: str) -> list[float]:
    return [float(m.group(2).replace(",", "")) for m in NUMBER_TOKEN.finditer(DATE_TOKEN.sub(" ", text))]


def _pairs(values: list[float]) -> list[tuple[float, float]]:
    """The row pairs a summary would reasonably compare."""
    if len(values) < 2:
        return []
    by_size = sorted(values)
    pairs = list(zip(values, values[1:])) + list(zip(by_size, by_size[1:]))
    pairs += [(values[0], values[-1]), (by_size[0], by_size[-1])]
    return pairs


def allowed_values(result: QueryResult, question: str = "") -> tuple[dict, dict, set[str]]:
    """(plain values, percent values, date strings) that a summary may use, each
    value mapped to a description of its source."""
    plain: dict[float, str] = {}
    pct: dict[float, str] = {}
    dates: set[str] = set()

    def add(target, value, source):
        target.setdefault(round(float(value), 9), source)

    add(plain, len(result.rows), "row count")
    for i, col in enumerate(result.columns):
        values = [r[i] for r in result.rows]
        nums = [float(v) for v in values if _is_num(v)]
        for v in values:
            if isinstance(v, str):
                dates.update(DATE_TOKEN.findall(v))
                for n in _numbers_in(v):
                    add(plain, n, f"text in column {col}")
        for v in nums:
            add(plain, v, f"cell in column {col}")
            add(pct, v, f"cell in column {col}")
            add(pct, v * 100, f"cell in column {col} as a %")
        if len(nums) >= 2:
            add(plain, len(nums), f"values in column {col}")
            total = sum(nums)
            add(plain, total, f"total of column {col}")
            add(plain, total / len(nums), f"average of column {col}")
            for a, b in _pairs(nums):
                add(plain, b - a, f"difference in column {col}")
                if a:
                    add(pct, (b - a) / abs(a) * 100, f"% change in column {col}")
            if total:
                for v in nums:
                    add(pct, v / total * 100, f"share of column {col}'s total")
    dates.update(DATE_TOKEN.findall(question))
    for n in _numbers_in(question):
        add(plain, n, "the question")
        add(pct, n, "the question")
    return plain, pct, dates


def _match(value: float, decimals: int, signed: bool, pool: dict) -> str | None:
    tol = 0.5 * 10 ** -decimals + 1e-9
    for candidate, source in pool.items():
        c = candidate if signed else abs(candidate)
        if abs(c - value) <= tol:
            return source
    return None


def check_numbers(text: str, result: QueryResult, question: str = "") -> NumberCheck:
    plain, pct, dates = allowed_values(result, question)
    numbers = []
    for d in DATE_TOKEN.findall(text):
        numbers.append(Number(d, 0.0, 0, False, False, "date in the result" if d in dates else None))
    for m in NUMBER_TOKEN.finditer(DATE_TOKEN.sub(" ", text)):
        sign, digits, percent, suffix = m.groups()
        value = float(digits.replace(",", ""))
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        if sign in ("-", "−"):
            value = -value
        pool = pct if percent else plain
        source = None if suffix else _match(value, decimals, bool(sign), pool)
        numbers.append(Number(m.group(0), value, decimals, bool(sign), bool(percent), source))
    return NumberCheck(numbers)


# ------------------------------------------------------------ template writer

def humanise(column: str) -> str:
    """Column alias to words: "avg_order_value" -> "average order value". A
    trailing "pct" is dropped, since the value is printed with a % sign."""
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", column).replace("_", " ").lower().split()
    if len(words) > 1 and words[-1] == "pct":
        words = words[:-1]
    return " ".join(WORDS.get(w, w) for w in words)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def unit(column: str, values: list) -> str:
    name = column.lower()
    if any(w in name for w in PCT_WORDS):
        return "pct"
    if any(w in name for w in MONEY_WORDS):
        return "money"
    return "count" if all(isinstance(v, int) for v in values if v is not None) else "decimal"


def fmt(value, kind: str, signed: bool = False) -> str:
    if value is None:
        return "(none)"
    sign = "+" if signed and value > 0 else ""
    if kind == "money":
        return f"{'-' if value < 0 else sign}${abs(value):,.2f}"
    if kind == "pct":
        return f"{sign}{value:,.1f}%"
    if kind == "count":
        return f"{sign}{value:,.0f}"
    return f"{sign}{value:,.2f}"


def _columns(result: QueryResult) -> tuple[list[int], list[int]]:
    """Label columns (text, or integer ids) and metric columns (other numbers)."""
    labels, metrics = [], []
    for i, col in enumerate(result.columns):
        vals = [r[i] for r in result.rows if r[i] is not None]
        numeric = bool(vals) and all(_is_num(v) for v in vals)
        is_id = col.lower().endswith("id") and all(isinstance(v, int) for v in vals)
        (metrics if numeric and not is_id else labels).append(i)
    return labels, metrics


def _label(row: tuple, cols: list[int], names: list[str]) -> str:
    parts = []
    for i in cols:
        v = row[i]
        text = "none" if v is None else str(v)
        parts.append(f"{humanise(names[i])} {text}" if names[i].lower().endswith("id") else text)
    if len(parts) > 1 and all("name" in names[i].lower() for i in cols):
        return " ".join(parts)  # first name + last name
    extra = [f"{humanise(names[i])}: {p}" for i, p in zip(cols[1:], parts[1:])]
    return parts[0] + (f" ({', '.join(extra)})" if extra else "")


def _group(cols: list[int], names: list[str]) -> tuple[str, str]:
    """What the rows are, singular and plural: ("country", "countries")."""
    if not cols:
        return "row", "rows"
    if len(cols) > 1 and all("name" in names[i].lower() for i in cols):
        return "name", "rows"
    word = humanise(names[cols[0]])
    if names[cols[0]].lower().endswith("id") or " " in word:
        return word, "rows"
    return word, _plural(word)


def _plural(word: str) -> str:
    if word.endswith("s"):
        return word
    return word[:-1] + "ies" if word.endswith("y") and word[-2:-1] not in "aeiou" else word + "s"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def write_template(question: str, result: QueryResult) -> str:
    rows, names = result.rows, result.columns
    if not rows:
        return "No rows matched the question."
    labels, metrics = _columns(result)
    kinds = {i: unit(names[i], [r[i] for r in rows]) for i in metrics}
    note = (f" Only the first {len(rows):,} rows came back, so this covers those rows only."
            if result.truncated else "")

    if len(rows) == 1:
        row = rows[0]
        figures = [f"{humanise(names[i])} {fmt(row[i], kinds[i])}" for i in metrics]
        if not labels:
            return "; ".join(_cap(f"{humanise(names[i])}: {fmt(row[i], kinds[i])}") for i in metrics) + "." + note
        head = _label(row, labels, names)
        return f"{head}: {'; '.join(figures)}." if figures else f"Answer: {head}."

    if not metrics:
        shown = [_label(r, labels, names) for r in rows[:5]]
        rest = " and the rest in the table" if len(rows) > 5 else ""
        return f"{len(rows):,} rows: {', '.join(shown)}{rest}." + note

    m = metrics[0]
    kind, name = kinds[m], humanise(names[m])
    lab = [_label(r, labels, names) for r in rows] if labels else [f"row {k + 1}" for k in range(len(rows))]
    vals = [r[m] for r in rows]
    known = [(v, l) for v, l in zip(vals, lab) if v is not None]
    # Totals and shares only for amounts that add up, over at least two values.
    additive = (kind in ("money", "count") and len(known) > 1
                and not any(w in names[m].lower() for w in NOT_ADDITIVE))
    total = sum(v for v, _ in known)
    with_value = "" if len(known) == len(rows) else " with a value"
    group, groups = _group(labels, names)
    extra = [humanise(names[i]) for i in metrics[1:]]
    also = f" Also in the table: {', '.join(extra)}." if extra else ""

    if not known:
        return f"{len(rows):,} rows, but no {name} values." + note

    time_like = len(known) > 1 and labels and all(
        isinstance(r[labels[0]], str) and TIME_LABEL.match(r[labels[0]]) for r in rows)
    if time_like and [r[labels[0]] for r in rows] == sorted(r[labels[0]] for r in rows):
        (v0, l0), (v1, l1) = known[0], known[-1]
        hi, lo = max(known, key=lambda x: x[0]), min(known, key=lambda x: x[0])
        text = f"{_cap(name)} by {group}, {l0} to {l1}: "
        if kind != "pct" and v0:
            text += (f"{fmt(v0, kind)} in {l0} and {fmt(v1, kind)} in {l1} "
                     f"({fmt((v1 - v0) / abs(v0) * 100, 'pct', True)}). Highest")
        else:
            text += "highest"
        text += f" in {hi[1]} ({fmt(hi[0], kind, kind == 'pct')}), lowest in {lo[1]} ({fmt(lo[0], kind, kind == 'pct')})."
        if additive:
            periods = groups if group in ("year", "month", "day", "quarter") else "periods"
            text += f" Total {fmt(total, kind)} over {len(known):,} {periods}{with_value}."
        return text + also + note

    ranked = sorted(known, key=lambda x: -x[0])

    def item(v, l, share=False):
        s = f"{l} with {fmt(v, kind)}"
        if share and additive and total:
            s += f" ({fmt(v / total * 100, 'pct')} of the total shown)"
        return s

    parts = [f"highest {item(*ranked[0], share=True)}"]
    if len(ranked) == 2:
        parts.append(f"then {item(*ranked[1])}")
    elif len(ranked) > 2:
        middle = [item(*x) for x in ranked[1:min(3, len(ranked) - 1)]]
        parts.append(f"then {_join(middle)}")
        parts.append(f"lowest {item(*ranked[-1])}")
    text = f"{_cap(name)} by {group} ({len(rows):,} {groups}): {'; '.join(parts)}."
    if additive:
        text += (f" These {len(rows):,} rows add up to {fmt(total, kind)}." if not with_value else
                 f" The {len(known):,} rows with a value add up to {fmt(total, kind)}.")
    return text + also + note


# ------------------------------------------------------------------- reports

@dataclass
class Report:
    text: str
    writer: str                   # template | claude
    check: NumberCheck
    fallback: bool = False        # the LLM text failed the check, so this is the template text
    rejected_text: str = ""       # the LLM text that failed, kept for the log
    notes: list[str] = field(default_factory=list)


def template_report(question: str, result: QueryResult) -> Report:
    text = write_template(question, result)
    return Report(text, "template", check_numbers(text, result, question))
