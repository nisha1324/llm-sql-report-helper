import json
from types import SimpleNamespace

import pytest

from sqlhelper.claude import ClaudeConfig
from sqlhelper.claude_report import INSTRUCTIONS, MAX_ROWS, ClaudeReportWriter, user_message
from sqlhelper.db import QueryResult
from sqlhelper.report import write_template

CFG = ClaudeConfig("test-model", 256, 1, 1.0, 5.0)
BY_COUNTRY = QueryResult(["country", "revenue"], [("USA", 523.06), ("Canada", 303.96), ("France", 195.10)])
QUESTION = "Which 3 countries generated the most revenue?"


def reply(text="", stop="end_turn", tokens=(300, 60)):
    content = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(content=content, stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]))


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))
        return self.replies.pop(0)


def writer(*replies):
    return ClaudeReportWriter(client=FakeClient(*replies), config=CFG)


GOOD = "USA led with $523.06, 51.2% of the three countries' total, ahead of Canada ($303.96) and France ($195.10)."
INVENTED = "USA led with $532.06, about 52% of revenue, ahead of Canada ($303.96)."


def test_request_shape_and_a_good_summary():
    w = writer(reply(GOOD))
    rep = w.write(QUESTION, BY_COUNTRY)
    assert (rep.text, rep.writer, rep.fallback, rep.check.ok) == (GOOD, "claude", False, True)
    (req,) = w.client.requests
    assert req["model"] == "test-model" and req["system"] == INSTRUCTIONS
    assert req["messages"] == [{"role": "user", "content": user_message(QUESTION, BY_COUNTRY)}]
    assert "output_config" not in req and "temperature" not in req
    content = req["messages"][0]["content"]
    assert QUESTION in content and "| USA       | 523.06    |" in content and "Result (3 rows)" in content
    assert (w.usage.calls, w.usage.retries, w.usage.input_tokens) == (1, 0, 300)


def test_invented_number_gets_one_retry_naming_it():
    w = writer(reply(INVENTED), reply(GOOD))
    rep = w.write(QUESTION, BY_COUNTRY)
    assert rep.text == GOOD and rep.writer == "claude" and "retry" in rep.notes[0]
    second = w.client.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == INVENTED
    assert "$532.06, 52%" in second[2]["content"]
    assert (w.usage.calls, w.usage.retries) == (2, 1)


def test_second_failure_falls_back_to_the_template():
    w = writer(reply(INVENTED), reply("USA led with $600."))
    rep = w.write(QUESTION, BY_COUNTRY)
    assert rep.fallback and rep.writer == "template" and rep.text == write_template(QUESTION, BY_COUNTRY)
    assert rep.check.ok and rep.rejected_text == "USA led with $600."
    assert "$600" in rep.notes[0] and w.usage.calls == 2


def test_unusable_replies_fall_back_without_retrying():
    for bad in (reply(stop="refusal"), reply("USA led", stop="max_tokens"), reply("")):
        w = writer(bad)
        rep = w.write(QUESTION, BY_COUNTRY)
        assert rep.fallback and rep.writer == "template" and w.usage.calls == 1


def test_big_or_empty_results_are_not_sent():
    big = QueryResult(["k", "n"], [(str(i), i) for i in range(MAX_ROWS + 1)])
    w = writer()
    rep = w.write("?", big)
    assert rep.writer == "template" and not rep.fallback and "more than the 50" in rep.notes[0]
    assert w.write("?", QueryResult(["n"], [])).text == "No rows matched the question."
    assert w.client.requests == []


def test_question_numbers_are_allowed():
    w = writer(reply("The top 3 are USA ($523.06), Canada ($303.96) and France ($195.10)."))
    assert w.write(QUESTION, BY_COUNTRY).writer == "claude"


def test_real_sdk_accepts_the_request():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        body = {"id": "msg_1", "type": "message", "role": "assistant", "model": "test-model",
                "content": [{"type": "text", "text": GOOD}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 410, "output_tokens": 45}}
        return httpx2.Response(200, json=body)

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    w = ClaudeReportWriter(client=client, config=CFG)
    rep = w.write(QUESTION, BY_COUNTRY)
    assert rep.text == GOOD and rep.writer == "claude"
    (body,) = sent
    assert body["model"] == "test-model" and body["system"] == INSTRUCTIONS and body["max_tokens"] == 400
    assert w.usage.output_tokens == 45
