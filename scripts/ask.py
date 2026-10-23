"""Ask one business question: SQL, guarded result and a checked summary.

Usage:
    python -m scripts.ask "Show revenue by year"                   # offline, no API key
    python -m scripts.ask --backend claude "Who are our best customers?"   # needs ANTHROPIC_API_KEY in .env

The offline path uses the semantic-layer backend and the template writer. The
claude path uses Claude for both the SQL and the summary; the summary must
still pass the number check, or the template text is shown instead.
"""
import argparse

from tabulate import tabulate

from scripts.evaluate import require_key
from sqlhelper.db import QueryRejected, run_query
from sqlhelper.report import template_report

SHOW_ROWS = 20


def ask(question: str, backend, writer=None) -> str:
    """Everything a user would see, as text."""
    answer = backend.answer(question)
    if answer.declined:
        return f"Declined ({answer.declined}): {answer.reason}"
    if not answer.sql:
        return f"No answer: {answer.reason}"
    try:
        result = run_query(answer.sql)
    except QueryRejected as e:
        return f"SQL:\n{answer.sql}\n\nBlocked by the guard: {e.reason}"
    report = writer.write(question, result) if writer else template_report(question, result)
    more = f"\n(first {SHOW_ROWS} of {len(result.rows)} rows)" if len(result.rows) > SHOW_ROWS else ""
    if not report.check.ok:
        check = f"WARNING, numbers not in the result: {', '.join(report.check.unsupported)}"
    elif report.check.numbers:
        check = f"all {len(report.check.numbers)} numbers traced to the result"
    else:
        check = "no numbers to check"
    lines = [f"SQL:\n{answer.sql}", "",
             tabulate(result.rows[:SHOW_ROWS], headers=result.columns, tablefmt="github", missingval="NULL") + more,
             "", f"Summary ({report.writer} writer; {check}):", report.text]
    lines += [f"Note: {n}" for n in report.notes]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--backend", choices=["offline", "claude"], default="offline")
    args = parser.parse_args()
    if args.backend == "claude":
        require_key()
        from sqlhelper.claude import ClaudeBackend
        from sqlhelper.claude_report import ClaudeReportWriter
        backend, writer = ClaudeBackend(), ClaudeReportWriter()
    else:
        from sqlhelper.offline import OfflineBackend
        backend, writer = OfflineBackend(), None
    print(ask(args.question, backend, writer))


if __name__ == "__main__":
    main()
