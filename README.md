# 🦅 ScrapeX — AI Super Agent for Web & Social Media

<p align="center">
  <img src="https://img.shields.io/badge/status-active-brightgreen" alt="Status">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python">
  <img src="https://img.shields.io/badge/docker-ready-brightgreen" alt="Docker">
</p>

> **The open-source Firecrawl alternative with social media superpowers.**

Scrape any website. Crawl entire domains. Extract structured data with AI. Scrape Twitter/X and Reddit. All from a single API — no API keys required for basic scraping.

---

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/hemoyt/ScrapeX.git && cd ScrapeX

# Run (Docker)
docker compose up -d

# That's it. API is live at http://localhost:8000
```

**API docs with interactive "Try it out":** [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 📡 API — 9 Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/scrape` | Scrape any URL → clean markdown, metadata, links |
| `POST` | `/api/v1/crawl` | Crawl an entire site (depth-controlled) |
| `GET` | `/api/v1/crawl/{id}` | Check crawl job status |
| `POST` | `/api/v1/search` | Search the web via DuckDuckGo |
| `POST` | `/api/v1/extract` | AI-powered structured data extraction |
| `POST` | `/api/v1/social/twitter` | Scrape Twitter/X profiles & tweets |
| `POST` | `/api/v1/social/reddit` | Scrape subreddits & posts with comments |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Interactive Swagger UI |

---

## 🐍 Python SDK

```python
from scrapex import ScrapeX

client = ScrapeX(base_url="http://localhost:8000")

# Scrape any website → clean markdown
page = client.scrape("https://books.toscrape.com")
print(page["title"])  # "All products | Books to Scrape - Sandbox"

# Search the web
results = client.search("best AI frameworks 2026")

# Crawl a site (async)
job = client.crawl("https://example.com", max_depth=2, max_pages=10)
status = client.crawl_status(job["id"])

# AI extraction (needs OpenRouter key)
data = client.extract(
    "https://books.toscrape.com",
    "Extract all book titles and prices as JSON"
)

# Social media
tweets = client.twitter("elonmusk", max_tweets=10)
posts = client.reddit("python", listing="hot", limit=5)
```

---

## 🔥 Features

- ✅ **Web scraping** — Any URL, clean markdown output, metadata, all links
- ✅ **JS rendering** — Handles SPAs and dynamic sites via Playwright
- ✅ **Site crawling** — Depth-controlled, domain-scoped, background jobs
- ✅ **AI extraction** — Describe what you want in natural language → structured JSON
- ✅ **Web search** — DuckDuckGo-powered, no API key needed
- ✅ **Reddit** — Posts, comments, subreddits via HTML scraping
- ✅ **Twitter/X** — Profile tweets via Nitter mirrors
- ✅ **Python SDK** — Full programmatic access with `pip install`
- ✅ **Docker** — One command: `docker compose up -d`
- 🔜 Instagram, LinkedIn, TikTok scrapers
- 🔜 Agent mode (autonomous browsing)
- 🔜 Real-time monitoring & webhooks

---

## ⚙️ Configuration

All via `.env` (copy from `.env.example`):

| Variable | Default | What it does |
|----------|---------|---------------|
| `SCRAPEX_OPENROUTER_API_KEY` | — | OpenRouter key for AI extraction |
| `SCRAPEX_AI_MODEL` | `google/gemini-flash-1.5` | LLM model for extraction |
| `SCRAPEX_DEBUG` | `false` | Enable verbose logging |
| `SCRAPEX_PROXY_URL` | — | Rotating proxy URL |

**Get an OpenRouter key (free):** [openrouter.ai/keys](https://openrouter.ai/keys)

---

## 🏗️ Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  Python SDK │────▶│  FastAPI      │────▶│  Playwright  │
│  (scrapex)  │     │  /api/v1/*    │     │  (JS render) │
└─────────────┘     └──────┬───────┘     └──────────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
         ┌────▼───┐  ┌────▼────┐  ┌────▼────┐
         │ httpx  │  │ OpenR.. │  │ Nitter  │
         │ + BS4  │  │ (AI)    │  │ (X.com) │
         └────────┘  └─────────┘  └─────────┘
```

---

## 🆚 vs Firecrawl

| | Firecrawl | **ScrapeX** |
|---|:--:|:--:|
| Web scraping | ✅ | ✅ |
| JS rendering | ✅ | ✅ |
| AI extraction | ✅ | ✅ |
| Site crawling | ✅ | ✅ |
| Web search | ✅ | ✅ |
| Agent mode | ✅ | 🔜 |
| **Social media** | ❌ | ✅ |
| **Anti-detection** | Basic | Enhanced |
| **Price** | $19-$249/mo | **Free & Open Source** |

---

## 📦 Project Structure

```
ScrapeX/
├── app/
│   ├── main.py              # FastAPI application
│   ├── config.py            # Settings & env vars
│   ├── routes/
│   │   ├── scrape.py        # /scrape, /crawl, /search
│   │   ├── social.py        # /social/twitter, /social/reddit
│   │   ├── extract.py       # /extract (AI)
│   │   └── health.py
│   ├── services/
│   │   ├── scraper.py       # HTTP + BeautifulSoup + Markdown
│   │   ├── browser.py       # Playwright JS rendering
│   │   ├── ai_extractor.py  # OpenRouter AI extraction
│   │   ├── reddit.py        # old.reddit.com HTML scraper
│   │   └── twitter.py       # Nitter-based X scraper
│   └── models/
│       └── __init__.py      # Pydantic schemas
├── sdk/python/
│   ├── setup.py
│   └── scrapex/
│       └── __init__.py      # Python client
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## 📄 License

MIT — free for personal and commercial use.

---

Built with ❤️ by [KanyouAI](https://kanyouai.com)
