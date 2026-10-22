"""Score a backend on the evaluation set: run its SQL through the guard and
compare the result with the gold result.

A backend is anything with a `name` and an `answer(question) -> Answer`
method (backend.py; offline.py now, an LLM later), so every backend is scored the same way.

Outcomes for an answerable question:
  correct    the result matches the gold result
  wrong      the SQL ran but the result differs (the dangerous one: a confident wrong number)
  rejected   the guard refused the SQL, or it didn't run
  abstained  the backend said it can't answer
  declined   the backend refused a question it should have answered
  error      the backend itself failed
Outcomes for a must-decline question:
  declined   refused before writing SQL (the right outcome)
  abstained  gave no answer, but no reason a user could act on
  rejected   wrote SQL, and the guard stopped it
  answered   wrote SQL that ran and returned data
  error      the backend itself failed
"""
from collections import Counter
from dataclasses import dataclass

from .backend import Answer, Backend
from .compare import compare
from .db import QueryRejected, run_query
from .questions import DeclineCase, Question

ANSWER_STATUSES = ("correct", "wrong", "rejected", "abstained", "declined", "error")
DECLINE_STATUSES = ("declined", "abstained", "rejected", "answered", "error")


@dataclass(frozen=True)
class Outcome:
    set: str
    id: str
    question: str
    level: str          # easy | medium | hard, or "decline"
    area: str           # business area, or the decline kind
    status: str
    detail: str         # the comparator's verdict, the guard's reason or the backend's reason
    sql: str | None
    declined_as: str | None = None

    @property
    def must_decline(self) -> bool:
        return self.level == "decline"


def _ask(backend: Backend, question: str) -> Answer | Exception:
    try:
        return backend.answer(question)
    except Exception as e:  # a backend bug shouldn't stop the run; it's scored as an error
        return e


def score_answerable(backend: Backend, q: Question, gold_rows: list[tuple], set_name: str) -> Outcome:
    a = _ask(backend, q.question)
    base = dict(set=set_name, id=q.id, question=q.question, level=q.level, area=q.area)
    if isinstance(a, Exception):
        return Outcome(**base, status="error", detail=f"{type(a).__name__}: {a}", sql=None)
    if a.declined:
        return Outcome(**base, status="declined", detail=a.reason, sql=None, declined_as=a.declined)
    if not a.sql:
        return Outcome(**base, status="abstained", detail=a.reason, sql=None)
    try:
        res = run_query(a.sql)
    except QueryRejected as e:
        return Outcome(**base, status="rejected", detail=e.reason, sql=a.sql)
    v = compare(gold_rows, res.rows, ordered=q.ordered, tolerance=q.tolerance)
    return Outcome(**base, status="correct" if v.match else "wrong", detail=v.detail, sql=a.sql)


def score_decline(backend: Backend, d: DeclineCase, set_name: str) -> Outcome:
    a = _ask(backend, d.question)
    base = dict(set=set_name, id=d.id, question=d.question, level="decline", area=d.kind)
    if isinstance(a, Exception):
        return Outcome(**base, status="error", detail=f"{type(a).__name__}: {a}", sql=None)
    if a.declined:
        return Outcome(**base, status="declined", detail=a.reason, sql=None, declined_as=a.declined)
    if not a.sql:
        return Outcome(**base, status="abstained", detail=a.reason, sql=None)
    try:
        res = run_query(a.sql)
    except QueryRejected as e:
        return Outcome(**base, status="rejected", detail=e.reason, sql=a.sql)
    return Outcome(**base, status="answered", detail=f"returned {len(res.rows)} row(s)", sql=a.sql)


def evaluate(backend: Backend, sets: dict[str, tuple[list[Question], list[DeclineCase]]]) -> list[Outcome]:
    """Score every question in every set. Gold results are computed once per gold query."""
    gold: dict[str, list[tuple]] = {}
    out = []
    for set_name, (answerable, decline) in sets.items():
        for q in answerable:
            if q.sql not in gold:
                gold[q.sql] = run_query(q.sql).rows
            out.append(score_answerable(backend, q, gold[q.sql], set_name))
        out.extend(score_decline(backend, d, set_name) for d in decline)
    return out


@dataclass(frozen=True)
class Summary:
    questions: int
    counts: dict[str, int]

    @property
    def answered(self) -> int:
        """Answerable questions where the backend's SQL ran and returned a result."""
        return self.counts.get("correct", 0) + self.counts.get("wrong", 0)

    @property
    def accuracy(self) -> float:
        return self.counts.get("correct", 0) / self.questions if self.questions else 0.0

    @property
    def precision(self) -> float | None:
        """How often a number it did give was right; None when it never answered."""
        return self.counts.get("correct", 0) / self.answered if self.answered else None


def summarise(outcomes: list[Outcome]) -> Summary:
    return Summary(len(outcomes), dict(Counter(o.status for o in outcomes)))
