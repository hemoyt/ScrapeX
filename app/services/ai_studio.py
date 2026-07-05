"""AI Studio — a direct, one-off prompt/response console for whatever AI
provider is configured. Unlike the research agent, it has no tools and no
loop: it's the fastest way to confirm "is my AI provider actually working"
and to try a model before pointing other features at it.
"""
from app.models import AIStudioRequest, AIStudioResponse
from app.services.ai_provider import disabled_reason, get_ai_client, provider_name, resolve_model


async def run_prompt(req: AIStudioRequest) -> AIStudioResponse:
    client = get_ai_client()
    if client is None:
        return AIStudioResponse(
            success=False, provider=provider_name(), model=resolve_model(req.model),
            status="no_llm", error=disabled_reason(),
        )

    messages = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.append({"role": "user", "content": req.prompt})

    model = resolve_model(req.model)
    try:
        response = await client.chat.completions.create(
            model=model, messages=messages, temperature=req.temperature, max_tokens=req.max_tokens,
        )
    except Exception as e:
        return AIStudioResponse(
            success=False, provider=provider_name(), model=model,
            status="error", error=f"{type(e).__name__}: {e}",
        )

    usage = getattr(response, "usage", None)
    return AIStudioResponse(
        success=True,
        provider=provider_name(),
        model=model,
        reply=response.choices[0].message.content,
        usage={
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        } if usage else {},
    )
