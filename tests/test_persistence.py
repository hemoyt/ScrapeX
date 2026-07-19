"""Tests for SQLite persistence: runs, datasets, and schedules survive a
simulated restart (fresh store objects hydrating from the same DB file)."""
import json

import pytest
import respx
from httpx import Response

from app.config import settings
from app.models import RunRequest, ScheduleRequest, SocialQueryType
from app.services import datasets as datasets_mod
from app.services import scheduler as scheduler_mod
from app.services.store import persistent_store

ALGOLIA = "https://hn.algolia.com/api/v1"


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Each test gets its own DB file and clean stores."""
    old = settings.db_file
    settings.db_file = str(tmp_path / "persist.sqlite3")
    persistent_store.reset()
    datasets_mod.run_store.clear()
    scheduler_mod.schedule_store.schedules.clear()
    delay = settings.run_page_delay
    settings.run_page_delay = 0
    yield
    settings.run_page_delay = delay
    datasets_mod.run_store.clear()
    scheduler_mod.schedule_store.schedules.clear()
    persistent_store.reset()
    settings.db_file = old


def _mock_hn(items=3):
    hits = [
        {"objectID": str(i), "title": f"Story {i}", "author": "pg",
         "points": 10, "num_comments": 1, "created_at": "2026-01-01T00:00:00Z"}
        for i in range(items)
    ]
    respx.get(f"{ALGOLIA}/search").mock(
        return_value=Response(200, json={"hits": hits, "page": 0, "nbPages": 1})
    )


def _fresh_stores():
    """Simulate a process restart: brand-new store objects, same DB file."""
    new_runs = datasets_mod.RunStore()
    new_runs.load()
    new_schedules = scheduler_mod.ScheduleStore()
    new_schedules.load()
    return new_runs, new_schedules


@respx.mock
async def test_run_and_items_survive_restart():
    _mock_hn(3)
    req = RunRequest(platform="hackernews", query_type=SocialQueryType.search,
                     identifier="ai", max_items=10)
    run = datasets_mod.run_store.create(req)
    await datasets_mod.execute_run(run.id)
    assert run.status == "SUCCEEDED"
    assert run.item_count == 3

    restored_runs, _ = _fresh_stores()
    restored = restored_runs.runs[run.id]
    assert restored.status == "SUCCEEDED"
    assert restored.item_count == 3
    ds = restored_runs.datasets[run.dataset_id]
    assert ds.count == 3          # cheap count, no hydration
    assert len(ds.items) == 3     # lazy hydration from SQLite
    assert ds.items[0]["text"] == "Story 0" or "Story" in json.dumps(ds.items[0])


async def test_inflight_run_marked_aborted_after_restart():
    req = RunRequest(platform="hackernews", query_type=SocialQueryType.search,
                     identifier="ai", max_items=10)
    run = datasets_mod.run_store.create(req)
    run.status = "RUNNING"
    datasets_mod.run_store.persist(run)

    restored_runs, _ = _fresh_stores()
    restored = restored_runs.runs[run.id]
    assert restored.status == "ABORTED"
    assert "restarted" in (restored.status_detail or "")


async def test_schedule_survives_restart():
    schedule = scheduler_mod.schedule_store.create(ScheduleRequest(
        platform="hackernews", query_type=SocialQueryType.search,
        identifier="rust", interval_minutes=30, name="rust watch",
    ))
    _, restored_schedules = _fresh_stores()
    restored = restored_schedules.schedules[schedule.id]
    assert restored.name == "rust watch"
    assert restored.interval_minutes == 30
    assert restored.enabled is True


async def test_prune_deletes_from_disk():
    old_limit = settings.run_history_limit
    settings.run_history_limit = 2
    try:
        req = RunRequest(platform="hackernews", query_type=SocialQueryType.search,
                         identifier="x", max_items=1)
        first = datasets_mod.run_store.create(req)
        datasets_mod.run_store.create(req)
        datasets_mod.run_store.create(req)  # evicts `first`
        restored_runs, _ = _fresh_stores()
        assert first.id not in restored_runs.runs
        assert len(restored_runs.runs) == 2
    finally:
        settings.run_history_limit = old_limit


async def test_persistence_disabled_with_empty_db_file():
    settings.db_file = ""
    persistent_store.reset()
    req = RunRequest(platform="hackernews", query_type=SocialQueryType.search,
                     identifier="ai", max_items=10)
    run = datasets_mod.run_store.create(req)
    restored_runs, _ = _fresh_stores()
    assert run.id not in restored_runs.runs  # memory-only, nothing on disk
