"""Clean pipeline: tidy scraped output, then run it through the configured AI.

Two layers, so `clean: true` always improves the response:

1. tidy_* — deterministic, works with no AI at all: strips leftover HTML,
   collapses whitespace, trims runaway text fields, and drops the raw
   platform payloads (`data[]`) that make responses hard to read.
2. summarize_* — when an AI provider is configured (SCRAPEX_AI_PROVIDER),
   the tidied content is summarized into a short plain-language `summary`.
   No provider -> summary stays null; the response is still tidy.
"""
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from app.models import SocialResponse
from app.services.ai_provider import AIJSONError, chat_json, get_ai_client, resolve_model

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")

# Text-bearing keys worth tidying inside raw item dicts
_TEXT_KEYS = {"text", "title", "selftext", "bio", "description", "body", "about"}

MAX_TEXT = 4000


def tidy_text(value: Optional[str]) -> Optional[str]:
    """Strip HTML tags, collapse whitespace, cap runaway length."""
    if not value:
        return value
    out = _TAG_RE.sub(" ", str(value))
    out = _WS_RE.sub(" ", out)
    out = _NL_RE.sub("\n\n", out)
    out = "\n".join(line.strip() for line in out.split("\n")).strip()
    return out[:MAX_TEXT]


def tidy_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Tidy an item dict in place: clean text fields, drop empty values."""
    for key in list(item.keys()):
        val = item[key]
        if key in _TEXT_KEYS and isinstance(val, str):
            item[key] = tidy_text(val)
        elif val is None or val == "" or val == [] or val == {}:
            del item[key]
    return item


def tidy_response(resp: SocialResponse) -> SocialResponse:
    """Deterministic cleanup of a social response: readable posts/profile,
    no raw payload blobs."""
    for post in resp.posts:
        post.text = tidy_text(post.text)
        if post.extra:
            post.extra = {k: v for k, v in post.extra.items() if v not in (None, "", [], {})}
    if resp.profile:
        resp.profile.bio = tidy_text(resp.profile.bio)
    resp.data = []  # the raw payloads are the noisy part
    return resp


def _post_lines(resp: SocialResponse, max_items: int = 25) -> List[str]:
    lines = []
    for p in resp.posts[:max_items]:
        stats = " ".join(f"{k}={v}" for k, v in list(p.stats.items())[:3])
        text = (p.text or "").replace("\n", " ")[:280]
        lines.append(f"- {('@' + p.author) if p.author else 'unknown'}: {text} ({stats})".strip())
    return lines


async def summarize_response(resp: SocialResponse, context: str) -> Optional[str]:
    """Plain-language summary of one platform response. None when no AI."""
    client = get_ai_client()
    if client is None or not resp.success:
        return None

    if resp.profile and not resp.posts:
        p = resp.profile
        content = (
            f"Profile @{p.username} on {resp.platform}: name={p.display_name}, "
            f"followers={p.followers}, following={p.following}, posts={p.posts_count}, "
            f"bio: {p.bio or '-'}"
        )
        ask = "Describe this profile in 2-3 plain sentences a non-technical person understands."
    elif resp.posts:
        content = "\n".join(_post_lines(resp))
        ask = (
            "Summarize what these posts are about in 3-5 short bullet points, "
            "plain language, then one line with the overall takeaway. "
            "Only use what's in the posts — no guessing."
        )
    else:
        return None

    try:
        response = await client.chat.completions.create(
            model=resolve_model(),
            messages=[
                {"role": "system", "content": "You turn raw scraped social data into short, clear, honest summaries."},
                {"role": "user", "content": f"Context: {context} on {resp.platform}\n\n{content}\n\n{ask}"},
            ],
            temperature=0.3,
            max_tokens=350,
        )
        return (response.choices[0].message.content or "").strip() or None
    except Exception:
        return None  # a failed summary never breaks the scrape


async def summarize_multi(query: str, results: Dict[str, SocialResponse]) -> Optional[str]:
    """One cross-platform summary for /social/search."""
    client = get_ai_client()
    if client is None:
        return None
    blocks = []
    for platform, resp in results.items():
        if resp.success and resp.posts:
            blocks.append(f"[{platform}]\n" + "\n".join(_post_lines(resp, max_items=8)))
    if not blocks:
        return None
    try:
        response = await client.chat.completions.create(
            model=resolve_model(),
            messages=[
                {"role": "system", "content": "You turn raw scraped social data into short, clear, honest summaries."},
                {
                    "role": "user",
                    "content": (
                        f"Search query: {query}\n\n" + "\n\n".join(blocks)
                        + "\n\nSummarize what people across these platforms are saying in 3-6 plain bullet "
                        "points, noting differences between platforms if any. Only use what's in the posts."
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=450,
        )
        return (response.choices[0].message.content or "").strip() or None
    except Exception:
        return None


MAX_CLEAN_ITEMS = 60      # rows sent to the AI per request — keeps token usage sane
MAX_CLEAN_CHARS = 14000   # hard cap on the JSON payload size sent to the AI


def _prep_items_for_ai(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
    """Deterministically tidy + cap items before sending to the AI."""
    prepped = [tidy_item(dict(item)) for item in items[:MAX_CLEAN_ITEMS]]
    truncated = len(items) > MAX_CLEAN_ITEMS
    return prepped, truncated


async def clean_with_prompt(items: List[Dict[str, Any]], prompt: str, context: str = "") -> Dict[str, Any]:
    """Reshape/clean a list of scraped rows per a free-text instruction — the
    UI's 'Clean with AI' box in the Data Viewer.

    Degrades gracefully: with no AI configured this still returns deterministic
    cleanup (HTML stripped, empty fields dropped) instead of failing. A
    malformed AI reply falls back the same way rather than erroring out."""
    prepped, truncated = _prep_items_for_ai(items)

    client = get_ai_client()
    if client is None:
        return {
            "items": prepped,
            "notes": (
                "AI not configured — showing deterministic cleanup only (HTML stripped, "
                "empty fields dropped). Add an AI provider in Settings to enable prompt-based cleaning."
            ),
            "truncated": truncated,
            "status": "no_llm",
        }

    payload = json.dumps(prepped, ensure_ascii=False, default=str)
    if len(payload) > MAX_CLEAN_CHARS:
        payload = payload[:MAX_CLEAN_CHARS]
        truncated = True

    try:
        result = await chat_json(
            client,
            model=resolve_model(),
            messages=[
                {
                    "role": "system",
                    "content": (
                        'You clean and reshape scraped data rows per the user\'s instructions. '
                        'Reply with JSON only: {"items": [...], "notes": "one short sentence on what you changed"}. '
                        "`items` must be a JSON array of flat objects — one per input row, unless the "
                        "instructions ask you to merge/split/filter rows. Never invent data that isn't in "
                        "the input; only reshape, rename, filter, dedupe, or summarize what's there."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        (f"Context: {context}\n\n" if context else "")
                        + f"Instructions: {prompt}\n\n"
                        + f"Data ({len(prepped)} rows{', truncated' if truncated else ''}):\n{payload}"
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=4000,
        )
    except AIJSONError:
        return {
            "items": prepped,
            "notes": "The AI's reply wasn't valid JSON, so the original (tidied) data is shown unchanged. Try a simpler instruction.",
            "truncated": truncated,
            "status": "error",
            "error": "AI response was not valid JSON",
        }
    except Exception as e:
        return {
            "items": prepped,
            "notes": "Cleaning failed — showing the original (tidied) data.",
            "truncated": truncated,
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
        }

    cleaned = result.get("items") if isinstance(result, dict) else result
    if not isinstance(cleaned, list):
        cleaned = prepped
    cleaned = [row if isinstance(row, dict) else {"value": row} for row in cleaned]
    notes = result.get("notes") if isinstance(result, dict) else None

    return {
        "items": cleaned,
        "notes": notes if isinstance(notes, str) else None,
        "truncated": truncated,
        "status": "ok",
    }


async def summarize_items(platform: str, context: str, items: List[Dict[str, Any]]) -> Optional[str]:
    """AI summary over dataset-run items (a readable overview of the whole run)."""
    client = get_ai_client()
    if client is None or not items:
        return None
    lines = []
    for item in items[:30]:
        text = str(item.get("text") or item.get("title") or "").replace("\n", " ")[:240]
        if text:
            lines.append(f"- {item.get('author', 'unknown')}: {text}")
    if not lines:
        return None
    try:
        response = await client.chat.completions.create(
            model=resolve_model(),
            messages=[
                {"role": "system", "content": "You turn raw scraped social data into short, clear, honest summaries."},
                {
                    "role": "user",
                    "content": (
                        f"Dataset of {len(items)} items scraped from {platform} for '{context}'. "
                        f"First {len(lines)} items:\n" + "\n".join(lines)
                        + "\n\nGive a 3-5 bullet plain-language overview of this dataset: main topics, "
                        "tone, anything notable. Only use what's shown."
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=400,
        )
        return (response.choices[0].message.content or "").strip() or None
    except Exception:
        return None
