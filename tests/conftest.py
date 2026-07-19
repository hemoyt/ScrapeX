import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.ratelimit import limiter
from app.services.cache import social_cache
from app.services.store import persistent_store


@pytest.fixture(scope="session", autouse=True)
def _tmp_db(tmp_path_factory):
    """Point run/schedule persistence at a throwaway SQLite file so tests
    never touch a real .scrapex_data.sqlite3 in the working directory."""
    settings.db_file = str(tmp_path_factory.mktemp("db") / "test.sqlite3")
    persistent_store.reset()
    yield
    persistent_store.reset()


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
