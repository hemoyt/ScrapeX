"""Scraping routes — /scrape, /crawl, /search."""
import uuid
from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.models import ScrapeRequest, ScrapeResponse, CrawlRequest, CrawlStatus, SearchRequest
from app.services import ScraperService, BrowserService, AIExtractor

router = APIRouter()

# In-memory crawl status store (replace with Redis in production)
crawl_jobs: dict = {}


@router.post("/scrape", response_model=ScrapeResponse)
async def scrape_url(req: ScrapeRequest):
    """Scrape a single URL. Returns clean markdown, metadata, and links."""
    result = {"success": False, "url": req.url}

    try:
        # Browser mode (JS rendering)
        if req.render_js:
            browser = BrowserService()
            try:
                browser_data = await browser.render(req.url)
                html = browser_data["html"]

                from bs4 import BeautifulSoup
                from markdownify import markdownify as md
                import re

                soup = BeautifulSoup(html, "lxml")
                for tag in soup.find_all(["script", "style", "nav", "footer"]):
                    tag.decompose()

                result["title"] = soup.title.get_text(strip=True) if soup.title else None
                result["content"] = md(str(soup.body or soup), heading_style="ATX", strip=["img"])
                result["html"] = html[:100000]
                result["screenshot"] = browser_data.get("screenshot")
            finally:
                await browser.stop()
        else:
            # Static mode
            scraper = ScraperService()
            try:
                data = scraper.scrape(req.url)
                result["title"] = data["title"]
                result["content"] = data["content"]
                result["metadata"] = data["metadata"]
                result["links"] = data["links"]
            finally:
                scraper.close()

        # AI extraction
        if req.extract_ai and result.get("content") and req.ai_prompt:
            extractor = AIExtractor()
            extracted = extractor.extract(
                content=result["content"],
                prompt=req.ai_prompt,
                url=req.url,
            )
            result["extracted"] = extracted

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


@router.post("/crawl", response_model=dict)
async def crawl_site(req: CrawlRequest, background_tasks: BackgroundTasks):
    """Start a crawl job. Returns a job ID to poll for status."""
    job_id = str(uuid.uuid4())[:8]
    crawl_jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "pages_scraped": 0,
        "total_pages": 0,
        "results": [],
    }

    background_tasks.add_task(_run_crawl, job_id, req)
    return {"id": job_id, "status": "queued", "url": req.url}


@router.get("/crawl/{job_id}", response_model=CrawlStatus)
async def get_crawl_status(job_id: str):
    """Get the status of a crawl job."""
    if job_id not in crawl_jobs:
        raise HTTPException(status_code=404, detail="Crawl job not found")
    return crawl_jobs[job_id]


async def _run_crawl(job_id: str, req: CrawlRequest):
    """Background crawl worker."""
    from urllib.parse import urlparse
    from collections import deque

    job = crawl_jobs[job_id]
    job["status"] = "running"

    scraper = ScraperService()
    visited = set()
    queue = deque([(req.url, 0)])

    try:
        while queue and len(visited) < req.max_pages:
            url, depth = queue.popleft()
            if url in visited or depth > req.max_depth:
                continue
            visited.add(url)

            try:
                data = scraper.scrape(url)
                result = ScrapeResponse(
                    success=True,
                    url=url,
                    title=data["title"],
                    content=data["content"][:10000],  # Truncate in crawl
                    metadata=data["metadata"],
                )
                job["results"].append(result.model_dump())
                job["pages_scraped"] = len(visited)

                # Queue internal links
                base = urlparse(req.url).netloc
                for link in data.get("links", [])[:15]:
                    if link.get("internal") and link["url"] not in visited:
                        queue.append((link["url"], depth + 1))

            except Exception:
                continue

        job["status"] = "completed"
        job["total_pages"] = len(visited)

    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
    finally:
        scraper.close()


@router.post("/search")
async def search_web(req: SearchRequest):
    """Search the web and optionally scrape results."""
    # Use DuckDuckGo HTML (no API key needed)
    import httpx
    from bs4 import BeautifulSoup

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": req.query},
                headers={"User-Agent": "Mozilla/5.0 ScrapeX/0.1"},
            )
            soup = BeautifulSoup(resp.text, "html.parser")

        results = []
        for r in soup.select(".result")[:req.num_results]:
            title_el = r.select_one(".result__title a")
            snippet_el = r.select_one(".result__snippet")
            link_el = r.select_one(".result__url")

            title = title_el.get_text(strip=True) if title_el else ""
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            link = link_el.get_text(strip=True) if link_el else ""

            # Clean link
            if link.startswith("//"):
                link = "https:" + link

            results.append({"title": title, "snippet": snippet, "url": link})

        # Optionally scrape each result
        if req.scrape_results and results:
            scraper = ScraperService()
            for r in results[:3]:  # Limit to 3 to avoid abuse
                try:
                    data = scraper.scrape(r["url"])
                    r["content"] = data["content"][:3000]
                except Exception:
                    r["content"] = None
            scraper.close()

        return {"success": True, "query": req.query, "results": results}

    except Exception as e:
        return {"success": False, "query": req.query, "error": str(e)}
