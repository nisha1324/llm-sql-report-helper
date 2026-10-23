"""Score a backend on the development set (eval/questions.yml) and the held-out
paraphrases (eval/heldout.yml).

Usage:
    python -m scripts.evaluate                    # offline backend (no API key needed)
    python -m scripts.evaluate --backend claude   # needs ANTHROPIC_API_KEY in .env

Writes results/EVAL_<backend>.md, results/eval_<backend>.csv and
results/charts/eval_<backend>.png.
"""
import argparse
import csv
import os
from collections import Counter
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from tabulate import tabulate  # noqa: E402

from sqlhelper.claude import ClaudeBackend  # noqa: E402
from sqlhelper.evaluate import ANSWER_STATUSES, DECLINE_STATUSES, Outcome, evaluate, summarise  # noqa: E402
from sqlhelper.offline import OfflineBackend  # noqa: E402
from sqlhelper.policy import ROOT  # noqa: E402
from sqlhelper.questions import AREAS, load_heldout, load_questions  # noqa: E402

RESULTS = ROOT / "results"
SETS = {"dev": "development set", "heldout": "held-out paraphrases"}
LEVELS = ("easy", "medium", "hard")
BACKENDS = {"offline": OfflineBackend, "claude": ClaudeBackend}
NEEDS_KEY = {"claude"}

# Reference palette, categorical slots 1-3 in fixed order (validated light-mode), and chart ink.
BUCKETS = {"correct": ("Right answer", "#2a78d6"),
           "wrong": ("Wrong answer (ran, but the result differs)", "#eb6834"),
           "none": ("No answer (said it can't, or was blocked)", "#1baf7a")}
SURFACE, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def bucket(o: Outcome) -> str:
    return o.status if o.status in ("correct", "wrong") else "none"


def headline(outcomes: list[Outcome]) -> list[list]:
    rows = []
    for key, label in SETS.items():
        s = summarise([o for o in outcomes if o.set == key and not o.must_decline])
        rows.append([label, s.questions, *(s.counts.get(st, 0) for st in ANSWER_STATUSES),
                     pct(s.accuracy), pct(s.precision)])
    return rows


def decline_table(outcomes: list[Outcome]) -> list[list]:
    rows = []
    for key, label in SETS.items():
        ds = [o for o in outcomes if o.set == key and o.must_decline]
        c = Counter(o.status for o in ds)
        right_kind = sum(1 for o in ds if o.status == "declined" and o.declined_as == o.area)
        rows.append([label, len(ds), *(c.get(st, 0) for st in DECLINE_STATUSES), right_kind])
    return rows


def breakdown(outcomes: list[Outcome], attr: str, values) -> list[list]:
    rows = []
    for v in values:
        row = [v]
        for key in SETS:
            group = [o for o in outcomes if o.set == key and not o.must_decline and getattr(o, attr) == v]
            if not group:
                row += ["-"]
                continue
            s = summarise(group)
            row.append(f"{s.counts.get('correct', 0)} / {s.questions} ({pct(s.accuracy)})")
        rows.append(row)
    return [r for r in rows if any(x != "-" for x in r[1:])]


def chart(outcomes: list[Outcome], name: str, path) -> None:
    groups = [(lvl, lambda o, lvl=lvl: o.level == lvl) for lvl in LEVELS] + [("all", lambda o: True)]
    labels, data = [], []
    for lvl, keep in groups:
        for key, short in (("dev", "dev"), ("heldout", "reworded")):
            g = [o for o in outcomes if o.set == key and not o.must_decline and keep(o)]
            labels.append(f"{lvl.capitalize()} · {short} ({len(g)})")
            data.append([sum(1 for o in g if bucket(o) == b) for b in BUCKETS])
    data, labels = data[::-1], labels[::-1]  # first group at the top

    fig, ax = plt.subplots(figsize=(10, 5.6), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    y = []
    pos = 0.0
    for i in range(len(data)):
        y.append(pos)
        pos += 1.0 if i % 2 == 0 else 1.6  # pairs (dev, reworded) sit together
    for (b, (lab, color)), col in zip(BUCKETS.items(), zip(*data)):
        lefts = [sum(row[:list(BUCKETS).index(b)]) / sum(row) * 100 for row in data]
        widths = [v / sum(row) * 100 for v, row in zip(col, data)]
        ax.barh(y, widths, left=lefts, height=0.8, color=color, edgecolor=SURFACE, linewidth=2, label=lab)
        for yy, left, w, v in zip(y, lefts, widths, col):
            if v:
                ax.text(left + w / 2, yy, str(v), ha="center", va="center", color="white" if b != "none" else INK,
                        fontsize=9, fontweight="bold")
    ax.set_yticks(y, labels, fontsize=9)
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter())
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED, labelcolor=INK2, length=0)
    ax.set_xlabel("Share of questions (numbers in the bars are question counts)", color=INK2)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, labelcolor=INK2, fontsize=9)

    dev = summarise([o for o in outcomes if o.set == "dev" and not o.must_decline])
    held = summarise([o for o in outcomes if o.set == "heldout" and not o.must_decline])
    dev_words = "the questions it was built from" if name == "offline" else "the development set"
    fig.suptitle(f"{name.capitalize()} path: {pct(dev.accuracy)} right on {dev_words}, "
                 f"{pct(held.accuracy)} once they're reworded",
                 x=0.02, ha="left", color=INK, fontsize=12.5, fontweight="bold")
    ax.set_title("32 business questions with known answers, asked twice; scored by running the SQL",
                 loc="left", color=INK2, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def require_key(env_path: Path = ROOT / ".env") -> None:
    """Stop before any request is sent if there's no API key. A key already set in
    the environment wins over .env; an empty one doesn't hide the .env key."""
    load_dotenv(env_path, override=not os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise SystemExit("The claude backend needs ANTHROPIC_API_KEY in .env (copy .env.example). "
                         "Nothing was sent. The offline backend needs no key: python -m scripts.evaluate")


def run_section(backend) -> list[str]:
    """Model, calls, retries and cost, for backends that call an API."""
    usage = getattr(backend, "usage", None)
    if usage is None:
        return []
    cfg = backend.config
    return ["## Run", "",
            f"Model `{cfg.model}`, run on {date.today().isoformat()}. LLM output can vary between runs, "
            "so this is one run, not a fixed score.", "",
            tabulate([["API calls", usage.calls],
                      ["retries after a guard rejection", usage.retries],
                      ["questions the retry fixed (SQL passed the guard)", usage.fixed_by_retry],
                      ["input tokens", f"{usage.input_tokens:,}"],
                      ["output tokens", f"{usage.output_tokens:,}"],
                      ["estimated cost (list price in config/claude.yml)", f"${usage.cost(cfg):.2f}"]],
                     headers=["", "this run"], tablefmt="github"), ""]


def write_report(outcomes: list[Outcome], name: str, run: list[str] = ()) -> str:
    by_id = {(o.set, o.id.removesuffix("p")): o for o in outcomes}
    pairs = [(by_id[("dev", o.id)], by_id[("heldout", o.id)]) for o in outcomes if o.set == "dev"]
    side = [[d.id, d.level if not d.must_decline else f"decline ({d.area})", d.area if not d.must_decline else "",
             d.status, h.status, h.detail if h.status != "correct" else ""]
            for d, h in pairs]
    lines = [
        f"# Evaluation: {name} backend", "",
        "Generated by `scripts/evaluate.py`. Don't edit by hand.", "",
        *run,
        "Each question is scored by running the backend's SQL through the guard and comparing the result "
        "with the gold result (`sqlhelper/compare.py`). The held-out set asks the same questions in other words "
        "and was written before the backend; the backend was never tuned on it.", "",
        "## Answerable questions", "",
        tabulate(headline(outcomes), headers=["set", "questions", *ANSWER_STATUSES, "accuracy",
                                              "right when it answered"], tablefmt="github"), "",
        "*Right when it answered* = correct / (correct + wrong): how far to trust a number it does give.", "",
        "## Must-decline questions", "",
        tabulate(decline_table(outcomes), headers=["set", "cases", *DECLINE_STATUSES, "declined for the right reason"],
                 tablefmt="github"), "",
        "## By level", "",
        tabulate(breakdown(outcomes, "level", LEVELS), headers=["level", *SETS.values()], tablefmt="github"), "",
        "## By business area", "",
        tabulate(breakdown(outcomes, "area", sorted(AREAS)), headers=["area", *SETS.values()], tablefmt="github"), "",
        "## Question by question", "",
        tabulate(side, headers=["id", "level", "area", "dev", "held-out", "held-out detail"], tablefmt="github"), "",
        "## Wrong answers and data returned on must-decline questions", "",
        "These are the failures a user wouldn't notice: SQL that ran and returned a plausible result.", "",
    ]
    bad = [o for o in outcomes if o.status in ("wrong", "answered")]
    for o in bad:
        lines += [f"### {o.id} ({SETS[o.set]}): {o.question}", "", f"{o.status}: {o.detail}", "",
                  f"```sql\n{o.sql}\n```", ""]
    if not bad:
        lines += ["None.", ""]
    reasons = Counter((SETS[o.set], o.detail.split(" (")[0]) for o in outcomes if o.status == "abstained")
    lines += ["## Why it abstained", "",
              tabulate([[s, r, n] for (s, r), n in sorted(reasons.items())], headers=["set", "reason", "questions"],
                       tablefmt="github"), ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="offline")
    args = parser.parse_args()
    if args.backend in NEEDS_KEY:
        require_key()
    backend = BACKENDS[args.backend]()
    outcomes = evaluate(backend, {"dev": load_questions(), "heldout": load_heldout()})

    (RESULTS / "charts").mkdir(parents=True, exist_ok=True)
    (RESULTS / f"EVAL_{backend.name}.md").write_text(write_report(outcomes, backend.name, run_section(backend)))
    with (RESULTS / f"eval_{backend.name}.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["set", "id", "level", "area", "status", "declined_as", "detail", "question", "sql"])
        for o in outcomes:
            w.writerow([o.set, o.id, o.level, o.area, o.status, o.declined_as or "", o.detail, o.question, o.sql or ""])
    chart(outcomes, backend.name, RESULTS / "charts" / f"eval_{backend.name}.png")
    for row in headline(outcomes):
        print(f"{row[0]}: accuracy {row[-2]}, right when it answered {row[-1]}")
    print(f"Wrote results/EVAL_{backend.name}.md, eval_{backend.name}.csv, charts/eval_{backend.name}.png")


if __name__ == "__main__":
    main()
