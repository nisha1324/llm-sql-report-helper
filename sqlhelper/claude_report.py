"""Claude report writer: a short summary of a query result, held to the same
number check as the template writer (sqlhelper/report.py).

The model sees the question and the result table, and writes 2-4 sentences.
Every number in its text must be traceable to the result. If any isn't, the
model is told exactly which numbers failed and gets one more try. If the
second text fails too, or the reply is cut off or refused, the report falls
back to the template text and says so, so an invented number never reaches
the reader.

Results with more than MAX_ROWS rows aren't sent: the model would summarise
rows it can't see. Those get the template text.
"""
from tabulate import tabulate

from .claude import ClaudeConfig, Usage, load_config
from .db import QueryResult
from .report import Report, check_numbers, template_report

MAX_ROWS = 50
CHECK_RETRIES = 1
MAX_TOKENS = 400

INSTRUCTIONS = """\
You write a short summary of a database query result for a manager at an online music store.

- 2 to 4 sentences of plain text: no headings, lists or markdown.
- Answer the question first, then add the one or two points from the result that matter most.
- Use only numbers that are in the result, or these, worked out from it: the number of rows,
  a column's total or average, the difference or % change between two rows, or a row's share
  of a column's total. Don't estimate or extrapolate.
- Write money as $1,234.56 and percentages with one decimal (12.3%). Don't shorten numbers
  ("$1.2k", "3x") or use ordinals ("2nd"); use words such as "second" instead.
- Don't describe the SQL, and don't give advice the result can't support.
If you're told some numbers couldn't be traced to the result, rewrite the summary without them.
"""


def result_table(result: QueryResult) -> str:
    return tabulate(result.rows, headers=result.columns, tablefmt="github", missingval="NULL",
                    disable_numparse=True)


def user_message(question: str, result: QueryResult) -> str:
    return f"Question: {question}\n\nResult ({len(result.rows)} rows):\n\n{result_table(result)}"


class ClaudeReportWriter:
    name = "claude"

    def __init__(self, client=None, config: ClaudeConfig | None = None):
        self.config = config or load_config()
        if client is None:
            import anthropic  # only needed for a real run
            client = anthropic.Anthropic()
        self.client = client
        self.usage = Usage()

    def _call(self, messages: list[dict]):
        resp = self.client.messages.create(model=self.config.model, max_tokens=MAX_TOKENS,
                                           system=INSTRUCTIONS, messages=messages)
        self.usage.calls += 1
        self.usage.input_tokens += resp.usage.input_tokens
        self.usage.output_tokens += resp.usage.output_tokens
        if resp.stop_reason in ("refusal", "max_tokens"):
            return None, f"the model's reply was unusable (stop_reason: {resp.stop_reason})"
        text = " ".join(b.text for b in resp.content if b.type == "text").strip()
        return (text, "") if text else (None, "the model's reply had no text")

    def write(self, question: str, result: QueryResult) -> Report:
        if not result.rows:
            return template_report(question, result)
        if len(result.rows) > MAX_ROWS:
            rep = template_report(question, result)
            rep.notes.append(f"{len(result.rows)} rows is more than the {MAX_ROWS} sent to the model, "
                             "so this is the template text")
            return rep
        messages = [{"role": "user", "content": user_message(question, result)}]
        last_failed = ""
        for attempt in range(CHECK_RETRIES + 1):
            text, problem = self._call(messages)
            if text is None:
                return self._fallback(question, result, last_failed, problem)
            check = check_numbers(text, result, question)
            if check.ok:
                rep = Report(text, "claude", check)
                if attempt:
                    rep.notes.append("passed the number check on the retry")
                return rep
            last_failed = text
            if attempt < CHECK_RETRIES:
                self.usage.retries += 1
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": "These numbers couldn't be traced to the result: "
                                                         f"{', '.join(check.unsupported)}. Rewrite the summary."}]
        return self._fallback(question, result, last_failed,
                              f"numbers not in the result: {', '.join(check.unsupported)}")

    @staticmethod
    def _fallback(question: str, result: QueryResult, failed_text: str, why: str) -> Report:
        rep = template_report(question, result)
        rep.fallback, rep.rejected_text = True, failed_text
        rep.notes.append(f"the Claude summary was not used ({why}), so this is the template text")
        return rep
