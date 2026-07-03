#!/usr/bin/env python3
"""Live smoke test: one cheap real query per platform + web search.

Run from the repo root:  python scripts/verify_platforms.py
Not part of CI (hits real networks); use it to see what actually works
from YOUR egress IP right now.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import SocialRequest, SocialQueryType as QT  # noqa: E402
from app.services.social_registry import get_platform  # noqa: E402
from app.services.search import SearchService  # noqa: E402

CHECKS = [
    ("reddit", QT.posts, "python", {}),
    ("reddit", QT.search, "fastapi", {}),
    ("twitter", QT.post, "https://x.com/jack/status/20", {}),
    ("twitter", QT.profile, "jack", {}),
    ("twitter", QT.posts, "jack", {}),
    ("bluesky", QT.profile, "bsky.app", {}),
    ("bluesky", QT.search, "ai", {}),
    ("hackernews", QT.search, "python", {}),
    ("mastodon", QT.profile, "Gargron@mastodon.social", {}),
    ("mastodon", QT.search, "python", {}),
    ("youtube", QT.search, "python tutorial", {}),
    ("youtube", QT.posts, "@mkbhd", {}),
    ("instagram", QT.profile, "instagram", {}),
    ("tiktok", QT.profile, "tiktok", {}),
    ("linkedin", QT.profile, "company/anthropic", {}),
    ("facebook", QT.profile, "nasa", {}),
]

STATUS_ICON = {"ok": "✅", "partial": "🟡", "blocked": "🚫", "error": "❌"}


async def check(platform: str, qt: QT, identifier: str, options: dict) -> str:
    try:
        svc = get_platform(platform)
        resp = await asyncio.wait_for(
            svc.fetch(SocialRequest(query_type=qt, identifier=identifier, limit=3, options=options)),
            timeout=45,
        )
        icon = STATUS_ICON.get(resp.status, "?")
        detail = f"{len(resp.posts)} posts" if resp.posts else (
            f"profile={resp.profile.username}" if resp.profile else (resp.error or "")[:60]
        )
        return f"{icon} {platform:11} {qt.value:8} {resp.status:8} via {resp.source or '-':16} {detail}"
    except Exception as e:
        return f"❌ {platform:11} {qt.value:8} exception  {type(e).__name__}: {str(e)[:50]}"


async def main() -> None:
    print("ScrapeX live platform verification\n" + "=" * 70)
    results = await asyncio.gather(*(check(*c) for c in CHECKS))
    print("\n".join(results))

    print("\nweb search:")
    s = SearchService()
    try:
        hits = await s.search("anthropic claude", 3)
        icon = "✅" if hits else "❌"
        print(f"{icon} search      {len(hits)} results" + (f" (top: {hits[0]['url']})" if hits else ""))
    finally:
        await s.aclose()


if __name__ == "__main__":
    asyncio.run(main())
