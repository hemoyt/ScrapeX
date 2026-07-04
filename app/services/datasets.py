"""Apify-style dataset runs.

The sync /social endpoints are bounded by one HTTP request: one page,
limit <= 50, and the per-platform timeout. A *run* removes those bounds:
it's a background job that paginates a platform (via fetch_page cursors)
until it has max_items, hits the run time budget, or the platform runs
out of data — pushing every item into a dataset you can page through and
export as JSON, NDJSON, or CSV.
"""
import asyncio
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models import DatasetInfo, RunInfo, RunRequest, SocialQueryType, SocialRequest
from app.services.social_registry import get_platform

PAGE_SIZE = 50  # per-page ask; platforms clamp to their own API maximums


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Dataset:
    def __init__(self, dataset_id: str, run_id: str, platform: str):
        self.id = dataset_id
        self.run_id = run_id
        self.platform = platform
        self.created_at = _now_iso()
        self.items: List[Dict[str, Any]] = []
        self._seen: set = set()

    def push(self, items: List[Dict[str, Any]], max_items: int) -> int:
        """Append items, deduped across pages. Returns how many were new."""
        added = 0
        for item in items:
            if len(self.items) >= max_items:
                break
            key = item.get("id") or item.get("url") or repr(sorted(item.items()))[:200]
            if key in self._seen:
                continue
            self._seen.add(key)
            self.items.append(item)
            added += 1
        return added

    def info(self) -> DatasetInfo:
        return DatasetInfo(
            id=self.id,
            run_id=self.run_id,
            platform=self.platform,
            item_count=len(self.items),
            created_at=self.created_at,
        )


class RunStore:
    """In-memory store for runs and their datasets (oldest evicted past the
    history limit). Swap for Redis/DB when you need persistence."""

    def __init__(self):
        self.runs: "OrderedDict[str, RunInfo]" = OrderedDict()
        self.datasets: "OrderedDict[str, Dataset]" = OrderedDict()
        self.requests: Dict[str, RunRequest] = {}
        self.abort_flags: Dict[str, bool] = {}

    def create(self, req: RunRequest) -> RunInfo:
        run_id = uuid.uuid4().hex[:12]
        dataset_id = uuid.uuid4().hex[:12]
        run = RunInfo(
            id=run_id,
            dataset_id=dataset_id,
            platform=req.platform.lower(),
            query_type=req.query_type.value,
            identifier=req.identifier,
            status="READY",
            max_items=min(req.max_items, settings.run_max_items),
        )
        self.runs[run_id] = run
        self.datasets[dataset_id] = Dataset(dataset_id, run_id, run.platform)
        self.requests[run_id] = req
        self.abort_flags[run_id] = False
        self._prune()
        return run

    def _prune(self):
        while len(self.runs) > settings.run_history_limit:
            old_id, old_run = self.runs.popitem(last=False)
            self.datasets.pop(old_run.dataset_id, None)
            self.requests.pop(old_id, None)
            self.abort_flags.pop(old_id, None)

    def clear(self):
        self.runs.clear()
        self.datasets.clear()
        self.requests.clear()
        self.abort_flags.clear()


run_store = RunStore()


def _items_from_response(resp) -> List[Dict[str, Any]]:
    """Normalize a page into dataset items: posts if the platform mapped them,
    the profile for profile queries, otherwise the raw payloads."""
    if resp.posts:
        return [p.model_dump(exclude_none=True) for p in resp.posts]
    if resp.profile:
        return [resp.profile.model_dump(exclude_none=True)]
    return [d for d in resp.data if isinstance(d, dict)]


async def execute_run(run_id: str) -> None:
    """Background worker: loop fetch_page until done, budget spent, or aborted."""
    run = run_store.runs.get(run_id)
    req = run_store.requests.get(run_id)
    if run is None or req is None:
        return
    dataset = run_store.datasets[run.dataset_id]

    run.status = "RUNNING"
    run.started_at = _now_iso()
    started = time.monotonic()
    deadline = started + settings.run_time_budget
    cursor: Optional[str] = None
    empty_pages = 0

    try:
        while len(dataset.items) < run.max_items:
            if run_store.abort_flags.get(run_id):
                run.status = "ABORTED"
                break
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                run.status = "TIMED_OUT"
                run.status_detail = (
                    f"Time budget of {settings.run_time_budget}s spent; "
                    f"collected {len(dataset.items)} items."
                )
                break

            page_limit = min(PAGE_SIZE, run.max_items - len(dataset.items))
            sreq = SocialRequest(
                query_type=SocialQueryType(run.query_type),
                identifier=run.identifier,
                limit=page_limit,
                options=req.options,
            )
            svc = get_platform(run.platform)
            try:
                resp, cursor = await asyncio.wait_for(
                    svc.fetch_page(sreq, cursor),
                    timeout=min(settings.social_timeout, remaining_time),
                )
            except asyncio.TimeoutError:
                if dataset.items:
                    run.status = "TIMED_OUT"
                    run.status_detail = f"Page fetch timed out; keeping {len(dataset.items)} items."
                else:
                    run.status = "FAILED"
                    run.error = f"Timed out after {settings.social_timeout}s on the first page."
                break
            except Exception as e:
                if dataset.items:
                    run.status = "SUCCEEDED"
                    run.status_detail = f"Stopped early on page error: {type(e).__name__}: {e}"
                else:
                    run.status = "FAILED"
                    run.error = f"{type(e).__name__}: {e}"
                break
            finally:
                await svc.aclose()

            run.pages_fetched += 1
            run.source = resp.source or run.source
            run.status_detail = resp.status

            if not resp.success:
                if dataset.items:
                    run.status = "SUCCEEDED"
                    run.status_detail = f"Stopped early: {resp.error}"
                else:
                    run.status = "FAILED"
                    run.error = resp.error or f"{run.platform} returned status={resp.status}"
                break

            added = dataset.push(_items_from_response(resp), run.max_items)
            run.item_count = len(dataset.items)
            empty_pages = empty_pages + 1 if added == 0 else 0

            # No continuation, or two pages of pure duplicates -> platform is done.
            if cursor is None or empty_pages >= 2:
                run.status = "SUCCEEDED"
                break

            await asyncio.sleep(settings.run_page_delay)
        else:
            run.status = "SUCCEEDED"
    except Exception as e:  # defensive: never leave a run stuck in RUNNING
        run.status = "FAILED"
        run.error = f"{type(e).__name__}: {e}"

    run.item_count = len(dataset.items)
    run.finished_at = _now_iso()
    run.duration_seconds = round(time.monotonic() - started, 2)
