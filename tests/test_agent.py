"""Research agent tests — scripted fake LLM driving the tool loop."""
import json
from types import SimpleNamespace

import pytest
import respx
from httpx import Response

from app.models import AgentRequest
from app.services import agent as agent_module
from app.services.agent import ResearchAgent

DDG_HTML = """
<html><body>
<div class="result">
  <h2 class="result__title"><a href="https://en.wikipedia.org/wiki/Claude_(AI)">Claude (AI) - Wikipedia</a></h2>
  <a class="result__snippet">Claude is a family of large language models developed by Anthropic.</a>
</div>
<div class="result">
  <h2 class="result__title"><a href="https://www.anthropic.com/">Anthropic</a></h2>
  <a class="result__snippet">AI safety and research company.</a>
</div>
</body></html>
"""


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _tool_call(call_id, name, args):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _response(message):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
    )


class FakeLLM:
    """Yields scripted responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


@pytest.fixture
def _search_mocked():
    with respx.mock:
        respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(
            return_value=Response(200, text=DDG_HTML)
        )
        yield


async def test_agent_tool_loop_and_citations(_search_mocked):
    fake = FakeLLM([
        _response(_msg(tool_calls=[_tool_call("c1", "web_search", {"query": "claude ai"})])),
        _response(_msg(content="Claude is Anthropic's LLM family [1], built by Anthropic [2].")),
    ])
    agent = ResearchAgent()
    agent.client = fake

    result = await agent.run(AgentRequest(query="What is Claude?"))

    assert result.success is True
    assert result.status == "ok"
    assert "[1]" in result.answer
    assert [s.id for s in result.sources] == [1, 2]
    assert result.sources[0].url == "https://en.wikipedia.org/wiki/Claude_(AI)"
    assert result.steps[0].tool == "web_search"
    assert result.usage["llm_calls"] == 2
    assert result.usage["tool_calls"] == 1
    # the tool message fed back to the LLM contains the [1] marker
    tool_messages = [m for m in fake.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages and "[1]" in tool_messages[0]["content"]


async def test_agent_max_steps_forces_synthesis(_search_mocked):
    # Always asks for another tool call; loop must terminate and force an answer.
    tool_response = _response(
        _msg(tool_calls=[_tool_call("c1", "web_search", {"query": "claude"})])
    )
    fake = FakeLLM([
        tool_response,
        _response(_msg(tool_calls=[_tool_call("c2", "web_search", {"query": "claude again"})])),
        _response(_msg(tool_calls=[_tool_call("c3", "web_search", {"query": "claude more"})])),
        _response(_msg(content="Final forced answer [1].")),
    ])
    agent = ResearchAgent()
    agent.client = fake

    result = await agent.run(AgentRequest(query="What is Claude?", depth="basic"))

    assert result.status == "max_steps_reached"
    assert result.answer == "Final forced answer [1]."
    # final synthesis call must not offer tools
    assert "tools" not in fake.calls[-1]


async def test_agent_tool_failure_becomes_text(_search_mocked):
    fake = FakeLLM([
        _response(_msg(tool_calls=[_tool_call("c1", "social_posts", {
            "platform": "reddit", "identifier": "python"})])),
        _response(_msg(content="Could not gather social data.")),
    ])
    agent = ResearchAgent()
    agent.client = fake

    with respx.mock:
        respx.get(url__startswith="https://old.reddit.com").mock(return_value=Response(500))
        respx.get(url__startswith="https://html.duckduckgo.com").mock(
            return_value=Response(200, text=DDG_HTML)
        )
        result = await agent.run(AgentRequest(query="What do redditors say?"))

    assert result.success is True
    tool_step = result.steps[0]
    assert tool_step.tool == "social_posts"
    assert "status=error" in tool_step.result_summary or "failed" in tool_step.result_summary.lower() \
        or "no posts" in tool_step.result_summary


async def test_agent_no_llm_degrades_to_search(_search_mocked, monkeypatch):
    agent = ResearchAgent()
    agent.client = None
    result = await agent.run(AgentRequest(query="What is Claude?", max_sources=5))
    assert result.success is True
    assert result.status == "no_llm"
    assert result.answer is None
    assert len(result.sources) == 2
    assert "AI PROVIDER" in result.error.upper()


async def test_agent_llm_error_reported(_search_mocked):
    class Boom:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise RuntimeError("model exploded")

    agent = ResearchAgent()
    agent.client = Boom()
    result = await agent.run(AgentRequest(query="What is Claude?"))
    assert result.success is False
    assert result.status == "error"
    assert "model exploded" in result.error


@respx.mock
def test_search_route_with_scores(client):
    respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(
        return_value=Response(200, text=DDG_HTML)
    )
    resp = client.post("/api/v1/search", json={"query": "claude ai"})
    body = resp.json()
    assert body["success"] is True
    assert body["answer"] is None  # no key, include_answer not requested
    assert len(body["results"]) == 2
    assert body["results"][0]["score"] >= body["results"][1]["score"]


@respx.mock
def test_search_ddg_challenge_falls_back(client):
    respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(return_value=Response(202))
    respx.get(url__startswith="https://lite.duckduckgo.com/lite/").mock(return_value=Response(202))
    respx.get(url__startswith="https://www.startpage.com/sp/search").mock(
        return_value=Response(200, text="""
        <div class="result">
          <a class="result-title" href="https://www.anthropic.com/">Anthropic</a>
          <p class="description">AI safety company</p>
        </div>
        """)
    )
    resp = client.post("/api/v1/search", json={"query": "anthropic"})
    body = resp.json()
    assert body["success"] is True
    assert body["results"][0]["url"] == "https://www.anthropic.com/"


def test_agent_route_no_llm(client):
    with respx.mock:
        respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(
            return_value=Response(200, text=DDG_HTML)
        )
        resp = client.post("/api/v1/agent", json={"query": "What is Claude?"})
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "no_llm"
    assert len(body["sources"]) == 2


async def _collect_stream(agent, req):
    return [event async for event in agent.run_stream(req)]


async def test_agent_stream_emits_ordered_events(_search_mocked):
    fake = FakeLLM([
        _response(_msg(tool_calls=[_tool_call("c1", "web_search", {"query": "claude ai"})])),
        _response(_msg(content="Claude is Anthropic's LLM family [1].")),
    ])
    agent = ResearchAgent()
    agent.client = fake

    events = await _collect_stream(agent, AgentRequest(query="What is Claude?"))
    types = [e["type"] for e in events]

    # tool_call before its result; sources + answer before the terminal done
    assert types == ["tool_call", "tool_result", "sources", "answer", "done"]
    assert events[0]["tool"] == "web_search"
    assert events[0]["id"] == "c1" and events[1]["id"] == "c1"
    assert events[1]["status"] == "ok"
    assert len(events[2]["sources"]) == 2
    assert "[1]" in events[3]["answer"]
    assert events[-1]["status"] == "ok"


async def test_agent_stream_no_llm(_search_mocked):
    agent = ResearchAgent()
    agent.client = None
    events = await _collect_stream(agent, AgentRequest(query="What is Claude?", max_sources=5))
    types = [e["type"] for e in events]
    assert types[0] == "tool_call" and types[-1] == "done"
    assert events[-1]["status"] == "no_llm"
    sources_event = next(e for e in events if e["type"] == "sources")
    assert len(sources_event["sources"]) == 2


def test_agent_stream_route_no_llm(client):
    with respx.mock:
        respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(
            return_value=Response(200, text=DDG_HTML)
        )
        resp = client.post("/api/v1/agent/stream", json={"query": "What is Claude?"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    payloads = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    assert payloads[-1]["type"] == "done"
    assert payloads[-1]["status"] == "no_llm"
    assert any(p["type"] == "sources" and len(p["sources"]) == 2 for p in payloads)
