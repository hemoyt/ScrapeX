"""Browser service — Playwright-based JS rendering for SPAs."""
import asyncio
import base64
import os
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, Page

from app.config import settings


def _find_chromium_executable() -> Optional[str]:
    """Locate a usable chromium when the Playwright-managed one is missing
    (e.g. a preinstalled browser at PLAYWRIGHT_BROWSERS_PATH from a different
    Playwright version)."""
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_path:
        candidate = os.path.join(browsers_path, "chromium")
        if os.path.exists(candidate):
            return candidate
    return None


class BrowserService:
    """Headless browser for JavaScript-rendered pages."""

    def __init__(self, headless: bool = True, timeout: int = 30):
        self.headless = headless
        self.timeout = timeout * 1000  # ms
        self._browser: Optional[Browser] = None
        self._playwright = None

    async def start(self):
        self._playwright = await async_playwright().start()
        launch_kwargs: Dict[str, Any] = {
            "headless": self.headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        proxy = settings.proxy_url or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        try:
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        except Exception:
            executable = _find_chromium_executable()
            if not executable:
                raise
            self._browser = await self._playwright.chromium.launch(
                executable_path=executable, **launch_kwargs
            )

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def render(self, url: str, wait_until: str = "networkidle") -> Dict[str, Any]:
        """Render a page with JavaScript and return HTML + screenshot."""
        if not self._browser:
            await self.start()

        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )

        page: Page = await context.new_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=self.timeout)
            html = await page.content()
            screenshot = None

            try:
                screenshot_bytes = await page.screenshot(full_page=False, type="png")
                screenshot = base64.b64encode(screenshot_bytes).decode("utf-8")
            except Exception:
                pass

            return {"html": html, "screenshot": screenshot}
        finally:
            await context.close()

    async def screenshot(self, url: str) -> Optional[str]:
        """Take a screenshot of a URL."""
        data = await self.render(url)
        return data.get("screenshot")
