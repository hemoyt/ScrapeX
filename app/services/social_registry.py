"""Registry of social platform scrapers."""
import asyncio
from typing import Dict, Type

from app.services.social_base import SocialPlatform, UNSUPPORTED
from app.services.reddit import RedditService

PLATFORM_CLASSES: Dict[str, Type[SocialPlatform]] = {
    "reddit": RedditService,
}


def get_platform(name: str) -> SocialPlatform:
    """Instantiate a platform scraper by name. Raises KeyError for unknown names."""
    return PLATFORM_CLASSES[name.lower()]()


def platform_names() -> list[str]:
    return sorted(set(PLATFORM_CLASSES))


def platform_matrix() -> dict:
    """Per-platform capability/reliability matrix (for /health and docs)."""
    matrix = {}
    seen = set()
    for name, cls in PLATFORM_CLASSES.items():
        if cls in seen:
            continue  # skip aliases like "x" -> twitter
        seen.add(cls)
        matrix[cls.name] = {
            qt.value: cap for qt, cap in cls.capabilities.items() if cap != UNSUPPORTED
        }
    return matrix


async def probe_platforms() -> dict:
    """Run each platform's cheap live probe concurrently."""
    seen: dict[str, SocialPlatform] = {}
    for cls in PLATFORM_CLASSES.values():
        if cls.name not in seen:
            seen[cls.name] = cls()

    names = list(seen)
    results = await asyncio.gather(
        *(asyncio.wait_for(seen[n].probe(), timeout=8) for n in names),
        return_exceptions=True,
    )
    return {
        n: (r if isinstance(r, str) else "down")
        for n, r in zip(names, results)
    }
