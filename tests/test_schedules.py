"""Tests for schedules (recurring runs) and run webhooks."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import respx
from httpx import Response

from app.config import settings
from app.services.datasets import run_store
from app.services.scheduler import schedule_store, tick

ALGOLIA = "https://hn.algolia.com/api/v1"


@pytest.fixture(autouse=True)
def _clean():
    run_store.clear()
    schedule_store.clear()
    delay = settings.run_page_delay
    settings.run_page_delay = 0
    yield
    settings.run_page_delay = delay
    run_store.clear()
    schedule_store.clear()


def _mock_hn():
    hits = [
        {"objectID": str(i), "title": f"Story {i}", "author": "pg",
         "points": 10, "num_comments": 1, "created_at": "2026-01-01T00:00:00Z"}
        for i in range(3)
    ]
    respx.get(f"{ALGOLIA}/search").mock(
        return_value=Response(200, json={"hits": hits, "page": 0, "nbPages": 1})
    )


BODY = {
    "platform": "hackernews",
    "query_type": "search",
    "identifier": "llm agents",
    "interval_minutes": 30,
    "max_items": 10,
}


def test_create_list_get_delete(client):
    resp = client.post("/api/v1/schedules", json=BODY)
    assert resp.status_code == 201
    schedule = resp.json()
    assert schedule["interval_minutes"] == 30
    assert schedule["enabled"] is True
    assert schedule["next_run_at"] is not None
    assert schedule["runs_started"] == 0

    assert len(client.get("/api/v1/schedules").json()) == 1
    assert client.get(f"/api/v1/schedules/{schedule['id']}").json()["id"] == schedule["id"]

    assert client.delete(f"/api/v1/schedules/{schedule['id']}").status_code == 204
    assert client.get(f"/api/v1/schedules/{schedule['id']}").status_code == 404
    assert client.delete("/api/v1/schedules/nope").status_code == 404


def test_create_rejects_unknown_platform(client):
    resp = client.post("/api/v1/schedules", json={**BODY, "platform": "myspace"})
    assert resp.status_code == 404
    resp = client.post("/api/v1/schedules", json={**BODY, "platform": "linkedin"})
    assert resp.status_code == 400  # linkedin has no search support


def test_pause_resume(client):
    schedule = client.post("/api/v1/schedules", json=BODY).json()
    paused = client.post(f"/api/v1/schedules/{schedule['id']}/pause").json()
    assert paused["enabled"] is False
    assert paused["next_run_at"] is None
    resumed = client.post(f"/api/v1/schedules/{schedule['id']}/resume").json()
    assert resumed["enabled"] is True
    assert resumed["next_run_at"] is not None


@respx.mock
def test_run_immediately_fires_a_run(client):
    _mock_hn()
    schedule = client.post(
        "/api/v1/schedules", json={**BODY, "run_immediately": True}
    ).json()
    assert schedule["runs_started"] == 1
    assert schedule["last_run_id"] is not None
    run = client.get(f"/api/v1/runs/{schedule['last_run_id']}")
    assert run.status_code == 200


@respx.mock
def test_run_now_endpoint(client):
    _mock_hn()
    schedule = client.post("/api/v1/schedules", json=BODY).json()
    resp = client.post(f"/api/v1/schedules/{schedule['id']}/run")
    assert resp.status_code == 202
    assert resp.json()["platform"] == "hackernews"
    assert client.get(f"/api/v1/schedules/{schedule['id']}").json()["runs_started"] == 1


@respx.mock
async def test_tick_fires_due_schedules_and_advances():
    _mock_hn()
    from app.models import ScheduleRequest, SocialQueryType

    schedule = schedule_store.create(ScheduleRequest(
        platform="hackernews", query_type=SocialQueryType.search,
        identifier="ai", interval_minutes=5,
    ))
    # Not due yet -> nothing fires
    assert await tick() == []

    # Force it due -> one run fires, next_run_at advances a full interval
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    schedule.next_run_at = past.isoformat()
    started = await tick()
    assert len(started) == 1
    assert schedule.runs_started == 1
    assert datetime.fromisoformat(schedule.next_run_at) > datetime.now(timezone.utc)

    # Let the fired execute_run task finish before the loop closes
    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)
    run = run_store.runs[started[0]]
    assert run.status == "SUCCEEDED"
    assert run.item_count == 3

    # Paused schedules never fire
    schedule_store.set_enabled(schedule.id, False)
    schedule.next_run_at = None
    assert await tick() == []


@respx.mock
async def test_webhook_fired_on_run_finish():
    _mock_hn()
    hook = respx.post("https://example.com/hook").mock(return_value=Response(200))
    from app.models import RunRequest, SocialQueryType
    from app.services.datasets import execute_run

    req = RunRequest(platform="hackernews", query_type=SocialQueryType.search,
                     identifier="ai", max_items=10,
                     webhook_url="https://example.com/hook")
    run = run_store.create(req)
    await execute_run(run.id)

    assert hook.called
    import json

    payload = json.loads(hook.calls.last.request.content)
    assert payload["event"] == "run.finished"
    assert payload["run"]["id"] == run.id
    assert payload["run"]["status"] == "SUCCEEDED"
    assert payload["run"]["item_count"] == 3


@respx.mock
async def test_webhook_failure_never_fails_the_run():
    _mock_hn()
    respx.post("https://example.com/dead").mock(side_effect=Exception("connection refused"))
    from app.models import RunRequest, SocialQueryType
    from app.services.datasets import execute_run

    req = RunRequest(platform="hackernews", query_type=SocialQueryType.search,
                     identifier="ai", max_items=10,
                     webhook_url="https://example.com/dead")
    run = run_store.create(req)
    await execute_run(run.id)
    assert run.status == "SUCCEEDED"
