"""AI-powered data extraction via any configured AI provider."""
from typing import Optional, Dict, Any

from app.services.ai_provider import AIJSONError, chat_json, disabled_reason, get_ai_client, resolve_model


class AIExtractor:
    """Extract structured data from web content using LLMs."""

    def __init__(self):
        # Async client — the sync one blocked the event loop inside routes.
        self.client = get_ai_client()

    async def extract(self, content: str, prompt: str, url: str = "") -> Dict[str, Any]:
        """Extract structured data from content using an AI prompt.

        Args:
            content: The page content (markdown/text)
            prompt: Natural language description of what to extract
            url: Source URL for context

        Returns:
            Parsed JSON with extracted data
        """
        if not self.client:
            return {"error": disabled_reason()}

        # Truncate content if too long (keep first ~15K chars is plenty for most extraction)
        content = content[:15000]

        system_prompt = (
            "You are a data extraction agent. Extract the requested information from "
            "the web page content below. Return ONLY valid JSON — no markdown, no explanation. "
            "If you cannot find something, use null. Be precise."
        )

        user_prompt = f"""URL: {url}

EXTRACTION REQUEST: {prompt}

PAGE CONTENT:
{content}

Return ONLY valid JSON with the extracted data."""

        try:
            return await chat_json(
                self.client,
                model=resolve_model(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
            )
        except AIJSONError as e:
            return {"raw_output": e.raw, "error": "Failed to parse AI response as JSON"}
        except Exception as e:
            return {"error": str(e)}
