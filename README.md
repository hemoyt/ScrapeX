# 🦅 ScrapeX — AI Research Agent for Web & Social Media

<p align="center">
  <img src="https://img.shields.io/badge/status-active-brightgreen" alt="Status">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python">
  <img src="https://img.shields.io/badge/docker-ready-brightgreen" alt="Docker">
  <img src="https://img.shields.io/badge/platforms-10-orange" alt="Platforms">
</p>

> **One API for public web & social data — plus a Tavily-style research agent that turns questions into cited answers.**

Ask a question → the agent searches the web and social platforms, scrapes what matters, and returns a markdown answer with `[n]` citations. Or hit the platforms directly: **Twitter/X, Reddit, YouTube, Bluesky, Hacker News, Mastodon, Instagram, TikTok, LinkedIn, Facebook** — all keyless, all through one unified endpoint.

The pain this solves: getting public web + social data normally means juggling 6 paid scraper APIs, half-dead libraries, and brittle scripts. ScrapeX is one self-hosted API with **honest status reporting** — when a platform blocks anonymous access, you get `status: "blocked"` and an explanation, never fabricated data.

---

## 🚀 Quick Start

```bash
git clone https://github.com/hemoyt/ScrapeX.git && cd ScrapeX
docker compose up -d
# Web UI at http://localhost:8000 — API docs at /docs
```

**Open http://localhost:8000 in your browser** — ScrapeX ships with a built-in UI (no build step, no Node):

- **Competitors** — type your product, AI discovers the competitors and pulls their social profiles + what Reddit/HN are saying about them. Plus a "track mentions" search across platforms.
- **Research** — ask a question, get a cited answer with sources and the agent's full tool trace.
- **Playground** — try every API endpoint with editable request bodies and pretty JSON.

```bash
# Ask the research agent (needs a free OpenRouter key for answers)
curl -X POST localhost:8000/api/v1/agent \
  -H 'Content-Type: application/json' \
  -d '{"query": "What are developers saying about AI agents this week?"}'

# Scrape a YouTube channel — no API key
curl -X POST localhost:8000/api/v1/social/youtube \
  -H 'Content-Type: application/json' \
  -d '{"query_type": "posts", "identifier": "@mkbhd", "limit": 5}'

# One keyword, five platforms, one call
curl -X POST localhost:8000/api/v1/social/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "open source llm", "platforms": ["reddit", "hackernews", "bluesky", "youtube"]}'
```

---

## 🤖 The Research Agent

`POST /api/v1/agent` — the Tavily-style core. The LLM runs a tool loop over ScrapeX's own capabilities (`web_search`, `scrape_url`, `social_search`, `social_posts`), registers every result as a numbered source, and answers with citations that map to real URLs.

```json
{
  "query": "Is the Rust vs Go debate still active?",
  "depth": "advanced",          // "basic" = 3 steps, "advanced" = 8
  "max_sources": 8,
  "include_social": true
}
```

Returns `{answer, sources[], steps[], usage, status}`. The `steps` array is a full trace of what the agent did. Without an OpenRouter key it degrades to search-only (`status: "no_llm"`) instead of failing.

`POST /api/v1/search` also accepts `"include_answer": true` for a one-shot cited answer over web results — the lightweight version of the agent.

---

## 📱 Social Platform Support (honest matrix)

Every platform speaks the same request shape via `POST /api/v1/social/{platform}`:

```json
{"query_type": "profile | posts | post | search", "identifier": "...", "limit": 10, "options": {}}
```

| Platform | profile | posts | post | search | Strategy (all keyless) |
|---|:--:|:--:|:--:|:--:|---|
| **Bluesky** | ✅ | ✅ | ✅ | ✅ | Official public AppView API |
| **Hacker News** | ✅ | ✅ | ✅ + comments | ✅ | Official Algolia API |
| **YouTube** | ✅ | ✅ | ✅ | ✅ | Innertube (YouTube's own JSON API) |
| **Reddit** | — | ✅ | ✅ + comments | ✅ | old.reddit.com HTML |
| **Mastodon** | ✅ | ✅ | ✅ | 🟡 | Instance REST API; search auth-gated on big instances → hashtag fallback |
| **Twitter/X** | ✅ | 🚫* | ✅ | 🚫* | fxtwitter → vxtwitter → syndication CDN chain |
| **Instagram** | 🟡 | 🟡 | 🟡 | — | web profile API + embed pages; rate-limits datacenter IPs |
| **TikTok** | 🟡 | 🟡† | 🟡 | — | embedded page JSON; Playwright fallback |
| **LinkedIn** | 🟡 | — | — | — | og:/meta salvage; login wall reported honestly |
| **Facebook** | 🟡 | — | — | — | og:/meta salvage; login wall reported honestly |

✅ reliable · 🟡 best-effort (may return `status: "partial"` or `"blocked"` with an explanation) · — unsupported (the API tells you)
\* Twitter timelines/search have no reliable keyless source since Nitter died; single tweets & profiles work great. Point `SCRAPEX_NITTER_INSTANCES` at a live mirror to re-enable them.
† TikTok video lists need JS hydration — works where Playwright can run.

Every response includes `status` (`ok | partial | blocked | error`), `source` (which strategy served it), normalized `posts[]`/`profile`, and the raw payload in `data[]`. Check `GET /health` for the capability matrix, or `GET /health?probe=true` for **live** platform reachability from your server.

---

## 📡 API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | 🆕 Built-in web UI (competitors, research, playground) |
| `POST` | `/api/v1/competitors` | 🆕 Discover a product's competitors + their socials & mentions |
| `POST` | `/api/v1/agent` | 🆕 Research agent → cited answer + sources + trace |
| `POST` | `/api/v1/social/{platform}` | 🆕 Unified social scraping (10 platforms) |
| `POST` | `/api/v1/social/search` | 🆕 One keyword across many platforms, concurrently |
| `POST` | `/api/v1/search` | Web search (DDG → Startpage fallback), optional AI answer |
| `POST` | `/api/v1/scrape` | Scrape any URL → clean markdown, metadata, links |
| `POST` | `/api/v1/crawl` | Crawl a site (background job) |
| `GET` | `/api/v1/crawl/{id}` | Crawl job status |
| `POST` | `/api/v1/extract` | AI structured extraction from any page |
| `POST` | `/api/v1/social/twitter`, `/social/reddit` | Legacy endpoints (still work, old body shapes accepted) |
| `GET` | `/health` | Health + platform capability matrix (`?probe=true` for live probes) |

---

## 🐍 Python SDK

```python
from scrapex import ScrapeX

client = ScrapeX(base_url="http://localhost:8000")  # api_key="sx-..." if auth is enabled

# Research agent
r = client.agent("What are people saying about the latest Claude release?", depth="advanced")
print(r["answer"])          # markdown with [1][2] citations
print(r["sources"])         # the URLs behind those citations

# Competitor discovery + analysis
report = client.competitors("Notion (note-taking app)")
for c in report["competitors"]:
    print(c["name"], c["website"], c["profiles"].get("twitter", {}).get("followers"))

# Unified social API
profile = client.social("bluesky", "profile", "bsky.app")
videos  = client.social("youtube", "posts", "@mkbhd", limit=5)
tweet   = client.social("twitter", "post", "https://x.com/jack/status/20")
top     = client.social("reddit", "posts", "python", listing="top")

# Cross-platform search
hits = client.social_search("ai agents", platforms=["reddit", "hackernews", "bluesky"])

# Web search with AI answer
result = client.search("best vector databases 2026", include_answer=True)

# Classic scraping still here
page = client.scrape("https://books.toscrape.com")
data = client.extract("https://books.toscrape.com", "book titles and prices as JSON")
```

---

## ⚙️ Configuration

All via `.env` (copy from `.env.example`), prefix `SCRAPEX_`:

| Variable | Default | What it does |
|----------|---------|---------------|
| `SCRAPEX_OPENROUTER_API_KEY` | — | Enables the agent, `include_answer`, and `/extract` ([free key](https://openrouter.ai/keys)) |
| `SCRAPEX_AI_MODEL` | `google/gemini-flash-1.5` | Model for extraction/answers |
| `SCRAPEX_AGENT_MODEL` | falls back to `AI_MODEL` | Model for the research agent (needs tool-calling) |
| `SCRAPEX_AGENT_MAX_STEPS` | `8` | Tool-loop budget for `depth: "advanced"` |
| `SCRAPEX_API_KEYS` | — | Comma-separated keys; **auth is enforced only when set** (Bearer or `X-API-Key`) |
| `SCRAPEX_RATE_LIMIT_REQUESTS` | `60` | Requests/minute per client IP (in-process) |
| `SCRAPEX_CACHE_TTL` | `300` | Seconds to cache social responses |
| `SCRAPEX_SOCIAL_TIMEOUT` | `20` | Per-platform timeout (s) |
| `SCRAPEX_NITTER_INSTANCES` | — | Comma-separated Nitter mirrors for Twitter timelines |
| `SCRAPEX_PROXY_URL` | — | Outbound proxy for scraping |
| `SCRAPEX_DEBUG` | `false` | Verbose logging |

---

## 🆚 vs Firecrawl / Tavily

| | Firecrawl | Tavily | **ScrapeX** |
|---|:--:|:--:|:--:|
| Web scraping + JS rendering | ✅ | ❌ | ✅ |
| Site crawling | ✅ | ❌ | ✅ |
| AI extraction | ✅ | ❌ | ✅ |
| Search with cited AI answer | ❌ | ✅ | ✅ |
| Research agent (tool loop + trace) | 🟡 | 🟡 | ✅ |
| **Social media (10 platforms)** | ❌ | ❌ | ✅ |
| Honest per-platform status | — | — | ✅ |
| Price | $19–$249/mo | $30+/mo | **Free & self-hosted** |

---

## 🧪 Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q                              # 59 tests, no network needed
python scripts/verify_platforms.py    # live smoke test from YOUR egress IP
uvicorn app.main:app --reload
```

CI runs the test suite on every push/PR (`.github/workflows/ci.yml`). The live verify script matters because scraping reliability depends on your server's IP reputation — run it after deploying.

### A note on honesty

Platforms fight scrapers. Anything marked 🟡 can break or get rate-limited without notice — when that happens ScrapeX tells you (`status`, `error`) rather than returning stale or fake data. Responses are cached (default 5 min) to keep your footprint small. Scrape responsibly and respect each platform's terms.

## 📦 Project Structure

```
ScrapeX/
├── app/
│   ├── main.py                  # FastAPI app, auth, rate limiting
│   ├── config.py                # Settings (SCRAPEX_* env vars)
│   ├── auth.py                  # Optional API-key auth
│   ├── routes/
│   │   ├── agent.py             # /agent — research agent
│   │   ├── scrape.py            # /scrape, /crawl, /search
│   │   ├── social.py            # /social/{platform}, /social/search
│   │   └── extract.py, health.py
│   ├── services/
│   │   ├── agent.py             # ResearchAgent tool loop
│   │   ├── search.py            # DDG → Startpage search chain
│   │   ├── social_base.py       # SocialPlatform base (cache, degradation)
│   │   ├── social_registry.py   # platform name → service
│   │   ├── twitter.py, reddit.py, youtube.py, bluesky.py,
│   │   ├── hackernews.py, mastodon.py, instagram.py, tiktok.py,
│   │   ├── linkedin_facebook.py
│   │   ├── scraper.py, browser.py, ai_extractor.py, cache.py, net.py
│   └── models/                  # Pydantic schemas
├── sdk/python/scrapex/          # Python client
├── scripts/verify_platforms.py  # live smoke test
├── tests/                       # pytest suite (mocked HTTP)
└── docker-compose.yml, Dockerfile
```

## 📄 License

MIT — free for personal and commercial use.

---

Built with ❤️ by [KanyouAI](https://kanyouai.com)
