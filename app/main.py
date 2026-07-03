"""ScrapeX — AI Super Agent for Web & Social Media Scraping."""
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.auth import require_api_key
from app.config import settings
from app.ratelimit import limiter
from app.routes import scrape, social, extract, health, agent

app = FastAPI(
    title="ScrapeX",
    description=(
        "AI-powered web & social media scraping agent. Scrape any website and "
        "10+ social platforms, run cited AI research with the /agent endpoint, "
        "and extract structured data with AI."
    ),
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

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
app.include_router(
    scrape.router, prefix="/api/v1", tags=["Scraping"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    social.router, prefix="/api/v1/social", tags=["Social Media"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    extract.router, prefix="/api/v1", tags=["AI Extraction"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    agent.router, prefix="/api/v1", tags=["Research Agent"],
    dependencies=[Depends(require_api_key)],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"success": False, "error": f"Rate limit exceeded: {exc.detail}", "code": 429},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": str(exc.detail), "code": exc.status_code},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": str(exc.errors()), "code": 422},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": str(exc), "code": 500},
    )


@app.get("/")
@limiter.exempt
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
            "social_search": "/api/v1/social/search",
            "agent": "/api/v1/agent",
        },
    }
