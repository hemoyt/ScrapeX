import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ratelimit import limiter
from app.services.cache import social_cache


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.fixture(autouse=True)
def _clear_cache():
    social_cache.clear()
    yield
    social_cache.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)
