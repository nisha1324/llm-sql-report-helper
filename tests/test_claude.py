import json
from types import SimpleNamespace

import pytest

from sqlhelper.claude import (ANSWER_SCHEMA, ClaudeBackend, ClaudeConfig, ClaudeOutputError, load_config,
                              parse_reply, system_prompt)
from sqlhelper.evaluate import evaluate
from sqlhelper.policy import DB_PATH
from sqlhelper.questions import DeclineCase, Question

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="run scripts/download_data.py first")
CFG = ClaudeConfig("test-model", 256, 1, 1.0, 5.0)


def reply(action="sql", sql="", kind="none", reason="", stop="end_turn", tokens=(100, 20)):
    text = json.dumps({"action": action, "sql": sql, "decline_kind": kind, "reason": reason})
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]))


class FakeClient:
    """Plays back scripted replies and records every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))  # snapshot: the backend reuses its list
        return self.replies.pop(0)


def backend(*replies, config=CFG):
    return ClaudeBackend(client=FakeClient(*replies), config=config, card="CARD")


def test_config_file():
    cfg = load_config()
    assert cfg.model.startswith("claude-") and cfg.guard_retries == 1 and cfg.max_tokens >= 256


def test_system_prompt_carries_the_schema_card():
    assert system_prompt("CARD").endswith("\nCARD")
    if DB_PATH.exists():
        prompt = system_prompt()
        assert "## Metric definitions" in prompt and "Not available (personal data)" in prompt


def test_parse_reply():
    ok = parse_reply(reply(sql=" SELECT 1 ", reason="revenue").content[0].text)
    assert ok.sql == "SELECT 1" and ok.reason == "revenue" and ok.declined is None
    no = parse_reply(reply("decline", kind="privacy", reason="emails are personal").content[0].text)
    assert no.declined == "privacy" and no.sql is None
    assert parse_reply(reply("abstain").content[0].text).reason == "the model abstained"
    for bad in ('not json', json.dumps({"action": "sql", "sql": " "}),
                json.dumps({"action": "decline", "decline_kind": "none"}), json.dumps({"action": "guess"})):
        with pytest.raises(ClaudeOutputError):
            parse_reply(bad)


def test_schema_is_strict():
    assert ANSWER_SCHEMA["additionalProperties"] is False
    assert set(ANSWER_SCHEMA["required"]) == set(ANSWER_SCHEMA["properties"])


@needs_db
def test_request_shape_and_good_sql():
    b = backend(reply(sql="SELECT COUNT(*) AS orders FROM Invoice"))
    a = b.answer("How many orders?")
    assert a.sql == "SELECT COUNT(*) AS orders FROM Invoice"
    (req,) = b.client.requests
    assert req["model"] == "test-model" and req["max_tokens"] == 256 and req["system"] == system_prompt("CARD")
    assert req["messages"] == [{"role": "user", "content": "How many orders?"}]
    assert req["output_config"] == {"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}}
    assert "temperature" not in req
    assert (b.usage.calls, b.usage.retries, b.usage.input_tokens, b.usage.output_tokens) == (1, 0, 100, 20)


@needs_db
def test_guard_rejection_gets_one_retry_with_the_reason():
    b = backend(reply(sql="SELECT Email FROM Customer"),
                reply(sql="SELECT COUNT(*) FROM Customer"))
    a = b.answer("How many customers?")
    assert a.sql == "SELECT COUNT(*) FROM Customer"
    second = b.client.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert json.loads(second[1]["content"])["sql"] == "SELECT Email FROM Customer"
    assert "column Customer.Email is restricted (personal data)" in second[2]["content"]
    assert (b.usage.calls, b.usage.retries, b.usage.fixed_by_retry) == (2, 1, 1)


@needs_db
def test_sql_errors_are_retried_too_and_retries_stop():
    b = backend(reply(sql="SELECT nope FROM Invoice"), reply(sql="SELECT Phone FROM Customer"),
                reply(sql="SELECT 1"))
    a = b.answer("?")
    assert a.sql == "SELECT Phone FROM Customer"  # out of retries: handed to the harness as is
    assert "no such column: nope" in b.client.requests[1]["messages"][2]["content"]
    assert len(b.client.replies) == 1 and b.usage.fixed_by_retry == 0


@needs_db
def test_no_retries_when_configured_off():
    b = backend(reply(sql="SELECT Email FROM Customer"), config=ClaudeConfig("m", 256, 0, 1, 5))
    assert b.answer("?").sql == "SELECT Email FROM Customer" and b.usage.calls == 1


@needs_db
def test_decline_after_a_rejection():
    b = backend(reply(sql="SELECT Email FROM Customer"), reply("decline", kind="privacy", reason="personal"))
    a = b.answer("Emails of our customers?")
    assert a.declined == "privacy" and b.usage.fixed_by_retry == 0


def test_decline_and_abstain_make_one_call():
    b = backend(reply("decline", kind="write", reason="read only"), reply("abstain", reason="which year?"))
    assert b.answer("Delete it").declined == "write"
    assert b.answer("Sales?").reason == "which year?"
    assert b.usage.calls == 2


def test_refusal_is_an_abstain_and_a_cut_off_reply_is_an_error():
    b = backend(reply(stop="refusal"), reply(stop="max_tokens"),
                SimpleNamespace(content=[], stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1,
                                                                                         output_tokens=0)))
    assert "refused" in b.answer("?").reason
    with pytest.raises(ClaudeOutputError, match="max_tokens=256"):
        b.answer("?")
    with pytest.raises(ClaudeOutputError, match="no text"):
        b.answer("?")
    assert b.usage.calls == 3


def test_cost():
    b = backend(reply(sql="", action="abstain", tokens=(2_000_000, 100_000)))
    b.answer("?")
    assert b.usage.cost(CFG) == pytest.approx(2.5)


@needs_db
def test_scored_by_the_harness_like_any_backend():
    gold = "SELECT COUNT(*) FROM Invoice"
    b = backend(reply(sql="SELECT Total FROM Invoice WHERE Email = 1"), reply(sql="SELECT COUNT(InvoiceId) FROM Invoice"),
                reply(sql="SELECT COUNT(*) FROM Customer"),
                reply("decline", kind="privacy"),
                reply(sql="SELECT Phone FROM Customer"), reply(sql="SELECT Fax FROM Customer"))
    answerable = [Question("a", "orders?", "kpi", "easy", gold, False), Question("b", "orders!", "kpi", "easy", gold, False)]
    decline = [DeclineCase("d1", "emails?", "privacy"), DeclineCase("d2", "phones?", "privacy")]
    out = {o.id: o for o in evaluate(b, {"dev": (answerable, decline)})}
    assert [out[k].status for k in ("a", "b", "d1", "d2")] == ["correct", "wrong", "declined", "rejected"]
    assert b.usage.fixed_by_retry == 1 and b.usage.retries == 2


def test_real_sdk_accepts_the_request(monkeypatch):
    """The real anthropic client, with the network swapped for a stub, sends the
    request the API expects and the backend reads the SDK's response objects."""
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        body = {"id": "msg_1", "type": "message", "role": "assistant", "model": "test-model",
                "content": [{"type": "text", "text": json.dumps(
                    {"action": "decline", "sql": "", "decline_kind": "no_data", "reason": "no cost data"})}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 1500, "output_tokens": 40}}
        return httpx2.Response(200, json=body)

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    b = ClaudeBackend(client=client, config=CFG, card="CARD")
    a = b.answer("Which genre has the best margin?")
    assert a.declined == "no_data" and a.reason == "no cost data"
    (body,) = sent
    assert body["output_config"]["format"]["schema"] == ANSWER_SCHEMA and body["model"] == "test-model"
    assert b.usage.input_tokens == 1500
