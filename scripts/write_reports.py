"""Write a checked summary for every gold answer, then test the number check by
planting errors in those summaries.

1. Each of the 32 development questions: gold SQL -> guard -> template writer
   -> number check. Every number has to be traced to the result.
2. Planted errors, on every figure in those summaries (cells and values worked
   out from them; labels such as years and numbers from the question are left
   alone): scaled by +-1/5/10/50%, a typo (two neighbouring digits swapped, or
   the last digit one off), and two figures of the same kind swapped with each
   other. Each changed text goes back through the checker.

Usage: python -m scripts.write_reports
Writes results/REPORTS.md, results/report_check.csv and results/charts/report_check.png.
"""
import csv
from collections import Counter, defaultdict
from itertools import combinations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from tabulate import tabulate  # noqa: E402

from sqlhelper.db import run_query  # noqa: E402
from sqlhelper.policy import ROOT  # noqa: E402
from sqlhelper.questions import load_questions  # noqa: E402
from sqlhelper.report import DATE_TOKEN, check_numbers, number_tokens, template_report  # noqa: E402

RESULTS = ROOT / "results"
SCALES = (-0.50, -0.10, -0.05, -0.01, 0.01, 0.05, 0.10, 0.50)
LABEL_SOURCES = ("text in column", "the question", "date in the result")
EXAMPLES = ("q01", "q03", "q10", "q20")

# Reference palette: categorical slot 1 (one series, so one hue) and chart ink.
BAR, SURFACE, INK, INK2, MUTED, RULE = "#2a78d6", "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#c3c2b7"


def figures(text: str, result, question: str) -> list:
    """(match, Number) for every number that is a figure, not a label."""
    checked = check_numbers(text, result, question).numbers
    dates = len(DATE_TOKEN.findall(text))  # check_numbers lists dates first, then numbers in order
    pairs = zip(number_tokens(text), checked[dates:], strict=True)
    return [(m, n) for m, n in pairs if not n.source.startswith(LABEL_SOURCES)]


def render(m, value: float) -> str:
    """Write `value` the way match `m` was written (sign, $, commas, decimals, %)."""
    sign, digits, percent, _ = m.groups()
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    body = f"{abs(value):,.{decimals}f}" if "," in digits or abs(value) >= 1000 else f"{abs(value):.{decimals}f}"
    if sign:
        sign = "-" if value < 0 else "+"
    return f"{sign}{'$' if '$' in m.group(0) else ''}{body}{percent}"


def typos(m) -> list[tuple[str, str]]:
    sign, digits, percent, _ = m.groups()
    prefix = sign + ("$" if "$" in m.group(0) else "")
    pos = [i for i, c in enumerate(digits) if c.isdigit()]
    out = []
    for a, b in zip(pos, pos[1:]):
        if digits[a] != digits[b] and b == a + 1:
            swapped = digits[:a] + digits[b] + digits[a] + digits[b + 1:]
            out.append(("typo: neighbouring digits swapped", prefix + swapped + percent))
    last = pos[-1]
    d = int(digits[last])
    for nd in {d - 1, d + 1} & set(range(10)):
        out.append(("typo: last digit one off", prefix + digits[:last] + str(nd) + digits[last + 1:] + percent))
    return out


def plant(text: str, result, question: str) -> list[dict]:
    figs = figures(text, result, question)
    cases = []

    def add(kind, original, new, new_text):
        if new_text != text:
            check = check_numbers(new_text, result, question)
            matched = next((n.source for n in check.numbers if n.text == new), "") if check.ok else ""
            cases.append({"kind": kind, "original": original, "written_as": new, "caught": not check.ok,
                          "matched": matched, "text": new_text})

    for m, n in figs:
        s, e = m.span()
        for k in SCALES:
            new = render(m, n.value * (1 + k))
            if new != m.group(0):
                add(f"scaled {k:+.0%}", m.group(0), new, text[:s] + new + text[e:])
        for kind, new in typos(m):
            add(kind, m.group(0), new, text[:s] + new + text[e:])
    for (m1, n1), (m2, n2) in combinations(figs, 2):
        if n1.percent == n2.percent and m1.group(0) != m2.group(0):
            (s1, e1), (s2, e2) = m1.span(), m2.span()
            add("two figures swapped", f"{m1.group(0)} <> {m2.group(0)}", "",
                text[:s1] + m2.group(0) + text[e1:s2] + m1.group(0) + text[e2:])
    return cases


def small_whole(token: str) -> bool:
    """A whole number under 100, such as a count of customers: the easiest kind
    to match by accident, since many small counts and differences are allowed."""
    digits = token.lstrip("+-$").rstrip("%")
    return "." not in digits and int(digits.replace(",", "")) < 100


def kind_order(kind: str) -> tuple:
    if kind.startswith("scaled"):
        return (0, abs(float(kind.split()[1].rstrip("%"))), kind)
    return (1 if kind.startswith("typo") else 2, 0, kind)


def chart(rates: list[tuple[str, int, int]], path) -> None:
    labels = [f"{k} ({n:,})" for k, _, n in rates][::-1]
    values = [100 * c / n for _, c, n in rates][::-1]
    fig, ax = plt.subplots(figsize=(10, 5.4), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    bars = ax.barh(range(len(values)), values, height=0.62, color=BAR, edgecolor=SURFACE, linewidth=2)
    for b, v in zip(bars, values):
        inside = v > 12
        ax.text(v - 1.2 if inside else v + 1.2, b.get_y() + b.get_height() / 2, f"{v:.0f}%",
                ha="right" if inside else "left", va="center", fontsize=9, fontweight="bold",
                color="white" if inside else INK)
    ax.set_yticks(range(len(values)), labels, fontsize=9)
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter())
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.tick_params(colors=MUTED, labelcolor=INK2, length=0)
    ax.set_xlabel("Share of changed summaries the check refused (number of test cases in brackets)", color=INK2)
    swapped = next(100 * c / n for k, c, n in rates if k == "two figures swapped")
    share = "none" if swapped == 0 else f"{swapped:.0f}%"
    fig.suptitle(f"The number check refuses most changed figures, but {share} of the swapped ones",
                 x=0.02, ha="left", color=INK, fontsize=12.5, fontweight="bold")
    ax.set_title("Errors planted in the summaries of the 32 gold answers", loc="left", color=INK2, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    answerable, _ = load_questions()
    reports, cases = [], []
    for q in answerable:
        result = run_query(q.sql)
        rep = template_report(q.question, result)
        if not rep.check.ok:
            raise SystemExit(f"{q.id}: the template wrote numbers it can't trace: {rep.check.unsupported}")
        reports.append((q, result, rep))
        for c in plant(rep.text, result, q.question):
            cases.append({"id": q.id, **c})

    by_kind = defaultdict(lambda: [0, 0])
    for c in cases:
        by_kind[c["kind"]][0] += c["caught"]
        by_kind[c["kind"]][1] += 1
    rates = [(k, *by_kind[k]) for k in sorted(by_kind, key=kind_order)]
    split = defaultdict(lambda: [0, 0])
    for c in cases:
        if c["kind"] != "two figures swapped":
            group = "whole number under 100" if small_whole(c["original"]) else "money, %, decimal or 100+"
            split[group][0] += c["caught"]
            split[group][1] += 1
    n_figs = sum(len(figures(r.text, res, q.question)) for q, res, r in reports)
    sources = Counter(n.source.split(" in column")[0].split(" of column")[0].split("'s total")[0]
                      for _, _, r in reports for n in r.check.numbers)

    lines = ["# Report writer: checked summaries", "",
             "Generated by `scripts/write_reports.py`. Don't edit by hand.", "",
             "Each development question's gold SQL is run through the guard, summarised by the template "
             "writer (`sqlhelper/report.py`) and checked: every number in the text must be traced to the result.", "",
             f"**{len(reports)} of {len(answerable)} summaries pass the check**, with "
             f"{sum(len(r.check.numbers) for _, _, r in reports)} numbers in all, {n_figs} of them figures "
             "(the rest are labels such as years, or numbers from the question).", "",
             "## Where the numbers came from", "",
             tabulate(sorted(sources.items(), key=lambda x: -x[1]), headers=["source", "numbers"], tablefmt="github"),
             "", "## Planted errors", "",
             "Every figure in the summaries above was changed one way at a time, and each changed text went "
             "back through the checker. A change that rounds back to the same text isn't counted.", "",
             tabulate([[k, n, c, f"{100 * c / n:.1f}%"] for k, c, n in rates],
                      headers=["change", "cases", "caught", "catch rate"], tablefmt="github"), "",
             "### By kind of figure (all changes except swaps)", "",
             tabulate([[g, n, c, f"{100 * c / n:.1f}%"] for g, (c, n) in sorted(split.items())],
                      headers=["original figure", "cases", "caught", "catch rate"], tablefmt="github"), "",
             "### Changed figures the check accepted", "",
             "These still matched a value the result allows (another cell, a total, a difference...). "
             "Swapped figures aren't listed: each value is still in the result, so the check can't see the swap.", ""]
    missed = [c for c in cases if not c["caught"] and c["kind"] != "two figures swapped"]
    rows = [[c["id"], c["kind"], c["original"], c["written_as"], c["matched"]] for c in missed]
    lines += [tabulate(rows, headers=["id", "change", "original", "written as", "accepted as"], tablefmt="github")
              if rows else "None.", ""]
    lines += ["## Every summary", ""]
    for q, res, rep in reports:
        lines += [f"### {q.id}: {q.question}", "", rep.text, "",
                  f"*{len(res.rows)} rows; numbers traced: "
                  + (", ".join(f"{n.text} ← {n.source}" for n in rep.check.numbers) or "none") + "*", ""]
    (RESULTS / "charts").mkdir(parents=True, exist_ok=True)
    (RESULTS / "REPORTS.md").write_text("\n".join(lines))
    with (RESULTS / "report_check.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "kind", "original", "written_as", "caught", "matched", "text"])
        w.writeheader()
        w.writerows(cases)
    chart(rates, RESULTS / "charts" / "report_check.png")
    for k, c, n in rates:
        print(f"{k:38s} {c:4d} / {n:4d}  {100 * c / n:5.1f}%")
    print(f"{len(reports)} summaries checked; wrote results/REPORTS.md, report_check.csv, charts/report_check.png")


if __name__ == "__main__":
    main()
