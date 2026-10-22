"""What every backend returns, and the interface the evaluation harness expects."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Answer:
    sql: str | None = None
    declined: str | None = None   # privacy | write | no_data
    reason: str = ""              # why it declined or abstained, or a note on the SQL


class Backend(Protocol):
    name: str

    def answer(self, question: str) -> Answer: ...
