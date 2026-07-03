"""ScrapeX — AI Super Agent for Web & Social Media Scraping."""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from datetime import datetime

from app.config import settings
from app.routes import scrape, social, extract, health

app = FastAPI(
    title="ScrapeX",
    description="AI-powered web & social media scraping agent. Scrape any website, Twitter, Reddit, and extract structured data with AI.",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(scrape.router, prefix="/api/v1", tags=["Scraping"])
app.include_router(social.router, prefix="/api/v1/social", tags=["Social Media"])
app.include_router(extract.router, prefix="/api/v1", tags=["AI Extraction"])


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": str(exc)},
    )


@app.get("/")
async def root():
    return {
        "name": "ScrapeX",
        "version": settings.app_version,
        "docs": "/docs",
        "endpoints": {
            "scrape": "/api/v1/scrape",
            "crawl": "/api/v1/crawl",
            "search": "/api/v1/search",
            "extract": "/api/v1/extract",
            "social": "/api/v1/social/{platform}",
        },
    }
