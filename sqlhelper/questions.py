"""Load and validate the evaluation set in eval/questions.yml."""
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from .compare import DEFAULT_TOLERANCE
from .policy import ROOT

QUESTIONS_PATH = ROOT / "eval" / "questions.yml"
HELDOUT_PATH = ROOT / "eval" / "heldout.yml"
AREAS = {"kpi", "trend", "geography", "product", "customer", "staff", "basket"}
LEVELS = {"easy", "medium", "hard"}
DECLINE_KINDS = {"privacy", "write", "no_data"}


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    area: str
    level: str
    sql: str
    ordered: bool = False
    tolerance: float = DEFAULT_TOLERANCE
    published: list | None = None


@dataclass(frozen=True)
class DeclineCase:
    id: str
    question: str
    kind: str
    naive_sql: str | None = None


def load_questions(path: Path = QUESTIONS_PATH) -> tuple[list[Question], list[DeclineCase]]:
    raw = yaml.safe_load(path.read_text())
    answerable, decline, seen = [], [], set()

    def check_id(item):
        qid = item.get("id")
        if not qid or qid in seen:
            raise ValueError(f"missing or duplicate id: {qid!r}")
        seen.add(qid)
        for key in ("question",):
            if not item.get(key):
                raise ValueError(f"{qid}: missing {key}")
        return qid

    for item in raw.get("answerable", []):
        qid = check_id(item)
        unknown = set(item) - {"id", "question", "area", "level", "sql", "ordered", "tolerance", "published"}
        if unknown:
            raise ValueError(f"{qid}: unknown fields {sorted(unknown)}")
        if item.get("area") not in AREAS or item.get("level") not in LEVELS or not item.get("sql"):
            raise ValueError(f"{qid}: needs sql, area in {sorted(AREAS)} and level in {sorted(LEVELS)}")
        answerable.append(Question(qid, item["question"], item["area"], item["level"], item["sql"].strip(),
                                   bool(item.get("ordered", False)),
                                   float(item.get("tolerance", DEFAULT_TOLERANCE)), item.get("published")))
    for item in raw.get("must_decline", []):
        qid = check_id(item)
        kind = item.get("kind")
        if kind not in DECLINE_KINDS:
            raise ValueError(f"{qid}: kind must be one of {sorted(DECLINE_KINDS)}")
        if kind in ("privacy", "write") and not item.get("naive_sql"):
            raise ValueError(f"{qid}: {kind} cases need naive_sql so the guard can be tested")
        decline.append(DeclineCase(qid, item["question"], kind, (item.get("naive_sql") or "").strip() or None))
    return answerable, decline


def load_heldout(path: Path = HELDOUT_PATH, originals: Path = QUESTIONS_PATH
                 ) -> tuple[list[Question], list[DeclineCase]]:
    """Paraphrases of the evaluation set. Each one keeps its original's gold SQL,
    level, area and decline kind, and gets the id `<original>p`."""
    answerable, decline = load_questions(originals)
    by_id = {q.id: q for q in [*answerable, *decline]}
    raw = yaml.safe_load(path.read_text())
    out_a, out_d, seen = [], [], set()
    for section, cls, out in (("answerable", Question, out_a), ("must_decline", DeclineCase, out_d)):
        for item in raw.get(section, []):
            of, text = item.get("of"), item.get("question")
            if set(item) != {"of", "question"} or not text:
                raise ValueError(f"held-out entries need exactly 'of' and 'question': {item}")
            original = by_id.get(of)
            if not isinstance(original, cls):
                raise ValueError(f"{of!r} is not an {section} question in {originals.name}")
            if of in seen:
                raise ValueError(f"{of} is paraphrased twice")
            if text.strip().lower() == original.question.strip().lower():
                raise ValueError(f"{of}: the paraphrase repeats the original")
            seen.add(of)
            out.append(replace(original, id=f"{of}p", question=text))
    return out_a, out_d
