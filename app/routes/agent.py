"""Research agent route — Tavily-style query -> cited answer + sources."""
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.models import AgentRequest, AgentResponse
from app.services.agent import ResearchAgent

router = APIRouter()


@router.post("/agent", response_model=AgentResponse)
async def research(req: AgentRequest):
    """Run the research agent: searches the web and social platforms, scrapes
    pages, and returns a markdown answer with [n] citations plus the sources.

    Requires SCRAPEX_OPENROUTER_API_KEY for answer synthesis; without it the
    endpoint still returns search results (status="no_llm")."""
    agent = ResearchAgent(model=req.model)
    return await agent.run(req)


@router.post("/agent/stream")
async def research_stream(req: AgentRequest):
    """Server-Sent Events variant of /agent for the chat UI.

    Streams the agent's work as it happens: one `tool_call` event when the agent
    decides to use a tool, a `tool_result` event when it returns, then `sources`,
    `answer`, and a terminal `done`. Each event is a JSON object on a `data:` line.

    Consume it with fetch() + a stream reader (not EventSource — this is POST)."""
    agent = ResearchAgent(model=req.model)

    async def event_stream():
        try:
            async for event in agent.run_stream(req):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # never leak a raw traceback into the stream
            yield f"data: {json.dumps({'type': 'done', 'status': 'error', 'error': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx/traefik)
        },
    )
