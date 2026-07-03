"""Research agent — Tavily-style: query in, cited answer + sources out.

An LLM (via OpenRouter) runs a tool-calling loop over ScrapeX's own
capabilities: web search, page scraping, and the social platform scrapers.
Every tool result is registered as a numbered source so the final answer can
cite [n] markers that map back to real URLs.

Degrades gracefully: with no OpenRouter key it still runs one web search and
returns sources with status="no_llm" instead of failing.
"""
import asyncio
import json
from datetime import date
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.models import (
    AgentRequest,
    AgentResponse,
    AgentSource,
    AgentStep,
    SocialQueryType,
    SocialRequest,
)
from app.services.scraper import ScraperService
from app.services.search import SearchService
from app.services.social_registry import get_platform, platform_names

MAX_TOOL_RESULT_CHARS = 6000
SEARCHABLE_PLATFORMS = ["reddit", "bluesky", "hackernews", "youtube", "mastodon", "twitter"]

SYSTEM_PROMPT = """You are ScrapeX Research Agent, a rigorous web & social media research assistant. Today is {today}.

Use the available tools to gather evidence BEFORE answering. Prefer 2-4 diverse, credible sources. Search social platforms when the question involves public opinion, trends, creators, or recent chatter.

Every tool result shows numbered sources like [3]. When you have enough evidence, reply with your final answer:
- Concise markdown, leading with the direct answer.
- Cite sources inline as [n] using ONLY numbers you were actually shown. Never invent a citation.
- If the evidence is thin or conflicting, say so explicitly.
- Do not call more tools once you can answer."""


def _build_tools(include_social: bool) -> List[dict]:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web. Returns titles, URLs and snippets as numbered sources.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "num_results": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "scrape_url",
                "description": "Fetch a web page and return its readable content (markdown).",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
        },
    ]
    if include_social:
        tools += [
            {
                "type": "function",
                "function": {
                    "name": "social_search",
                    "description": "Search a social platform for posts about a topic.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "platform": {"type": "string", "enum": SEARCHABLE_PLATFORMS},
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                        },
                        "required": ["platform", "query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "social_posts",
                    "description": "Get a user's/channel's/subreddit's recent posts on a social platform.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "platform": {"type": "string", "enum": sorted(set(platform_names()) - {"x", "hn"})},
                            "identifier": {"type": "string", "description": "username, @handle, subreddit or URL"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                        },
                        "required": ["platform", "identifier"],
                    },
                },
            },
        ]
    return tools


class ResearchAgent:
    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.agent_model or settings.ai_model
        self.client: Optional[AsyncOpenAI] = None
        if settings.openrouter_api_key:
            self.client = AsyncOpenAI(
                base_url=settings.openrouter_base_url,
                api_key=settings.openrouter_api_key,
            )
        self._sources: Dict[str, AgentSource] = {}  # url -> source
        self._steps: List[AgentStep] = []
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0, "tool_calls": 0}

    # --- source registry ---

    def _register_source(self, url: str, title: str = "", snippet: str = "", platform: str = "web") -> int:
        if url in self._sources:
            return self._sources[url].id
        source = AgentSource(
            id=len(self._sources) + 1,
            url=url,
            title=(title or "")[:200],
            snippet=(snippet or "")[:400],
            platform=platform,
        )
        self._sources[url] = source
        return source.id

    # --- tool execution ---

    async def _execute_tool(self, name: str, args: dict) -> str:
        try:
            if name == "web_search":
                return await self._tool_web_search(args)
            if name == "scrape_url":
                return await self._tool_scrape(args)
            if name == "social_search":
                return await self._tool_social(args, SocialQueryType.search, args.get("query", ""))
            if name == "social_posts":
                return await self._tool_social(args, SocialQueryType.posts, args.get("identifier", ""))
            return f"Unknown tool: {name}"
        except Exception as e:
            # Tool failures become text the model can react to, never exceptions.
            return f"Tool {name} failed: {type(e).__name__}: {e}"

    async def _tool_web_search(self, args: dict) -> str:
        service = SearchService()
        try:
            results = await service.search(args["query"], min(int(args.get("num_results", 5)), 8))
        finally:
            await service.aclose()
        if not results:
            return "No web results found."
        lines = []
        for r in results:
            sid = self._register_source(r["url"], r["title"], r["snippet"])
            lines.append(f"[{sid}] {r['title']} — {r['url']}\n{r['snippet']}")
        return "\n\n".join(lines)

    async def _tool_scrape(self, args: dict) -> str:
        url = args["url"]
        scraper = ScraperService()
        try:
            data = await asyncio.to_thread(scraper.scrape, url)
        finally:
            scraper.close()
        content = (data.get("content") or "")[:MAX_TOOL_RESULT_CHARS]
        sid = self._register_source(url, data.get("title") or url, content[:300])
        return f"[{sid}] {data.get('title') or url} — {url}\n{content}"

    async def _tool_social(self, args: dict, query_type: SocialQueryType, identifier: str) -> str:
        platform = args["platform"]
        svc = get_platform(platform)
        resp = await svc.fetch(SocialRequest(
            query_type=query_type,
            identifier=identifier,
            limit=min(int(args.get("limit", 5)), 10),
        ))
        if not resp.posts:
            return f"{platform} returned no posts (status={resp.status}): {resp.error or 'no results'}"
        lines = []
        for post in resp.posts:
            url = post.url or f"https://{platform}"
            title = (post.text or "")[:120]
            sid = self._register_source(url, title, post.text or "", platform=platform)
            stats = ", ".join(f"{k}: {v}" for k, v in list(post.stats.items())[:4])
            lines.append(
                f"[{sid}] @{post.author or 'unknown'} on {platform} ({post.created_at or 'n/a'}; {stats})\n"
                f"{(post.text or '')[:500]}\n{url}"
            )
        header = "" if resp.status == "ok" else f"(note: {platform} status={resp.status}: {resp.error})\n"
        return header + "\n\n".join(lines)

    # --- main loop ---

    async def run(self, req: AgentRequest) -> AgentResponse:
        if self.client is None:
            return await self._no_llm_fallback(req)

        model = req.model or self.model
        max_steps = 3 if req.depth == "basic" else settings.agent_max_steps
        tools = _build_tools(req.include_social)
        messages: List[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(today=date.today().isoformat())},
            {"role": "user", "content": req.query},
        ]

        answer: Optional[str] = None
        status = "ok"
        try:
            for step_num in range(1, max_steps + 1):
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.2,
                    max_tokens=2000,
                )
                self._track_usage(response)
                message = response.choices[0].message

                if not message.tool_calls:
                    answer = message.content
                    break

                messages.append({
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in message.tool_calls
                    ],
                })

                async def run_call(tc):
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = await self._execute_tool(tc.function.name, args)
                    return tc, args, result

                results = await asyncio.gather(*(run_call(tc) for tc in message.tool_calls))
                for tc, args, result in results:
                    self._usage["tool_calls"] += 1
                    self._steps.append(AgentStep(
                        step=step_num,
                        tool=tc.function.name,
                        args=args,
                        result_summary=result[:200],
                    ))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result[:MAX_TOOL_RESULT_CHARS],
                    })

            if answer is None:
                # Out of steps — force a final synthesis without tools.
                messages.append({
                    "role": "user",
                    "content": "Answer the original question NOW using the sources you gathered. Cite [n].",
                })
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=2000,
                )
                self._track_usage(response)
                answer = response.choices[0].message.content
                status = "max_steps_reached"

        except Exception as e:
            return AgentResponse(
                success=False,
                query=req.query,
                sources=self._sorted_sources(req.max_sources),
                steps=self._steps,
                usage=self._usage,
                status="error",
                error=f"{type(e).__name__}: {e}",
            )

        return AgentResponse(
            success=True,
            query=req.query,
            answer=answer,
            sources=self._sorted_sources(req.max_sources),
            steps=self._steps,
            usage=self._usage,
            status=status,
        )

    def _track_usage(self, response) -> None:
        self._usage["llm_calls"] += 1
        usage = getattr(response, "usage", None)
        if usage:
            self._usage["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
            self._usage["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0

    def _sorted_sources(self, max_sources: int) -> List[AgentSource]:
        # Return every registered source (so [n] citations always resolve),
        # capped well above max_sources which mainly bounds per-tool result sizes.
        return sorted(self._sources.values(), key=lambda s: s.id)[: max(max_sources, 20)]

    async def _no_llm_fallback(self, req: AgentRequest) -> AgentResponse:
        """No OpenRouter key: still useful — one web search, sources only."""
        result_text = await self._tool_web_search({"query": req.query, "num_results": req.max_sources})
        self._steps.append(AgentStep(step=1, tool="web_search", args={"query": req.query},
                                     result_summary=result_text[:200]))
        return AgentResponse(
            success=True,
            query=req.query,
            answer=None,
            sources=self._sorted_sources(req.max_sources),
            steps=self._steps,
            usage=self._usage,
            status="no_llm",
            error="No SCRAPEX_OPENROUTER_API_KEY configured — returning search results without an AI answer.",
        )
