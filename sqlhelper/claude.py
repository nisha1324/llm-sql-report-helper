"""Claude backend: the schema card in the system prompt, one JSON answer per question.

The reply is held to a JSON schema by the API (structured outputs): an action
(sql | decline | abstain), the SQL, a decline kind and a reason. If the guard
rejects the SQL, the model sees the guard's reason and tries again, up to
`guard_retries` times (config/claude.yml). Whatever it returns last goes to
the harness, which runs it through the same guard as every other backend.

Needs ANTHROPIC_API_KEY (scripts/evaluate.py reads it from a gitignored .env).
The tests use a fake client, so nothing here calls the API without a key.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from .backend import Answer
from .db import QueryRejected, run_query
from .policy import ROOT
from .schema import build_card

CONFIG_PATH = ROOT / "config" / "claude.yml"
DECLINE_KINDS = ("privacy", "write", "no_data")

# The decline rules are definitions, not word lists, so the prompt borrows no
# wording from either question set; the schema card supplies the facts.
INSTRUCTIONS = """\
You answer business questions about an online music store by writing one SQLite query.
The schema card below is everything you know about the database. Reply with JSON only.

Choose one action:
- "sql": the question can be answered from the tables below. Put one read-only SELECT
  (or WITH ... SELECT) in "sql" and say in "reason" which metric definition you used.
- "decline": the question must not be answered. Set "decline_kind":
  - "privacy": answering needs a column listed as "Not available (personal data)",
    directly or by working something out from it.
  - "write": it asks you to change the data in any way. You can only read.
  - "no_data": it needs data the card says the store doesn't have.
  Give the user a one-sentence reason they can act on.
- "abstain": the question is too ambiguous to answer with one query. Say what is unclear.

Rules for the SQL:
- Use the metric definitions on the card exactly ("revenue", "orders", "active customers"...).
- Name every column you select; never SELECT *. Personal-data columns are blocked.
- Give each output column a short alias. Order rankings, and break ties by name or id.
- Keep money unrounded or round to 2 decimals; percentages to 1 decimal.
- If you get a message that the guard rejected your query, fix the query or, if the
  question can't be answered without the blocked data, decline.
Leave "sql" empty unless the action is "sql", and set "decline_kind" to "none" unless
the action is "decline".
"""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["sql", "decline", "abstain"]},
        "sql": {"type": "string"},
        "decline_kind": {"type": "string", "enum": [*DECLINE_KINDS, "none"]},
        "reason": {"type": "string"},
    },
    "required": ["action", "sql", "decline_kind", "reason"],
    "additionalProperties": False,
}


class ClaudeOutputError(Exception):
    """The model's reply couldn't be used (cut off, not JSON, or contradictory)."""


@dataclass(frozen=True)
class ClaudeConfig:
    model: str
    max_tokens: int
    guard_retries: int
    price_input: float
    price_output: float


def load_config(path: Path = CONFIG_PATH) -> ClaudeConfig:
    raw = yaml.safe_load(path.read_text())
    cfg = ClaudeConfig(str(raw["model"]), int(raw["max_tokens"]), int(raw["guard_retries"]),
                       float(raw["price_per_mtok"]["input"]), float(raw["price_per_mtok"]["output"]))
    if cfg.guard_retries < 0 or cfg.max_tokens < 1:
        raise ValueError("guard_retries must be >= 0 and max_tokens >= 1")
    return cfg


def system_prompt(card: str | None = None) -> str:
    return INSTRUCTIONS + "\n" + (card if card is not None else build_card())


def parse_reply(text: str) -> Answer:
    """Turn the model's JSON into an Answer, refusing contradictory replies."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeOutputError(f"reply is not JSON: {text[:80]!r}") from e
    action, sql, kind = data.get("action"), (data.get("sql") or "").strip(), data.get("decline_kind")
    reason = (data.get("reason") or "").strip()
    if action == "sql":
        if not sql:
            raise ClaudeOutputError("action is sql but the sql is empty")
        return Answer(sql=sql, reason=reason)
    if action == "decline":
        if kind not in DECLINE_KINDS:
            raise ClaudeOutputError(f"decline without a valid kind: {kind!r}")
        return Answer(declined=kind, reason=reason)
    if action == "abstain":
        return Answer(reason=reason or "the model abstained")
    raise ClaudeOutputError(f"unknown action: {action!r}")


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0          # extra calls made because the guard rejected the SQL
    fixed_by_retry: int = 0   # questions whose last SQL passed the guard after a rejection

    def cost(self, cfg: ClaudeConfig) -> float:
        return (self.input_tokens * cfg.price_input + self.output_tokens * cfg.price_output) / 1e6


class ClaudeBackend:
    name = "claude"

    def __init__(self, client=None, config: ClaudeConfig | None = None, card: str | None = None):
        self.config = config or load_config()
        self.system = system_prompt(card)
        if client is None:
            import anthropic  # only needed for a real run
            client = anthropic.Anthropic()
        self.client = client
        self.usage = Usage()

    def _call(self, messages: list[dict]) -> str:
        resp = self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=self.system,
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
        )
        self.usage.calls += 1
        self.usage.input_tokens += resp.usage.input_tokens
        self.usage.output_tokens += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            return json.dumps({"action": "abstain", "sql": "", "decline_kind": "none",
                               "reason": "the model refused to answer (stop_reason: refusal)"})
        if resp.stop_reason == "max_tokens":
            raise ClaudeOutputError(f"reply cut off at max_tokens={self.config.max_tokens}")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise ClaudeOutputError(f"no text in the reply (stop_reason: {resp.stop_reason})")
        return text

    def answer(self, question: str) -> Answer:
        messages = [{"role": "user", "content": question}]
        retries_left = self.config.guard_retries
        while True:
            text = self._call(messages)
            answer = parse_reply(text)
            if not answer.sql:
                return answer
            try:
                run_query(answer.sql)
            except QueryRejected as e:
                if not retries_left:
                    return answer  # out of retries: the harness scores it as rejected
                retries_left -= 1
                self.usage.retries += 1
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"The guard rejected that query: {e.reason}. "
                                                         "Reply again with the same JSON format."}]
                continue
            if retries_left < self.config.guard_retries:
                self.usage.fixed_by_retry += 1
            return answer
