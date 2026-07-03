"""AI-powered data extraction using OpenRouter."""
import json
from typing import Optional, Dict, Any
from openai import OpenAI
from app.config import settings


class AIExtractor:
    """Extract structured data from web content using LLMs."""

    def __init__(self):
        self.client = None
        if settings.openrouter_api_key:
            self.client = OpenAI(
                base_url=settings.openrouter_base_url,
                api_key=settings.openrouter_api_key,
            )

    def extract(self, content: str, prompt: str, url: str = "") -> Dict[str, Any]:
        """Extract structured data from content using an AI prompt.

        Args:
            content: The page content (markdown/text)
            prompt: Natural language description of what to extract
            url: Source URL for context

        Returns:
            Parsed JSON with extracted data
        """
        if not self.client:
            return {"error": "No OpenRouter API key configured. Set SCRAPEX_OPENROUTER_API_KEY."}

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
            response = self.client.chat.completions.create(
                model=settings.ai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content
            return json.loads(raw)

        except json.JSONDecodeError:
            return {"raw_output": raw, "error": "Failed to parse AI response as JSON"}
        except Exception as e:
            return {"error": str(e)}
