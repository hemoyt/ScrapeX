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

import httpx

from app.config import settings
from app.models import DatasetInfo, RunInfo, RunRequest, SocialQueryType, SocialRequest
from app.services.social_registry import get_platform
from app.services.store import persistent_store

PAGE_SIZE = 50  # per-page ask; platforms clamp to their own API maximums


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedupe_key(item: Dict[str, Any]) -> str:
    return item.get("id") or item.get("url") or repr(sorted(item.items()))[:200]


class Dataset:
    def __init__(self, dataset_id: str, run_id: str, platform: str,
                 created_at: Optional[str] = None, hydrated: bool = True):
        self.id = dataset_id
        self.run_id = run_id
        self.platform = platform
        self.created_at = created_at or _now_iso()
        self._items: List[Dict[str, Any]] = []
        self._seen: set = set()
        # Datasets restored from disk defer loading their items until first
        # access, so a large history doesn't slow startup.
        self._hydrated = hydrated

    @property
    def items(self) -> List[Dict[str, Any]]:
        if not self._hydrated:
            self._items = persistent_store.load_items(self.id)
            self._seen = {_dedupe_key(i) for i in self._items}
            self._hydrated = True
        return self._items

    @property
    def count(self) -> int:
        if not self._hydrated:
            return persistent_store.count_items(self.id)
        return len(self._items)

    def push(self, items: List[Dict[str, Any]], max_items: int) -> int:
        """Append items, deduped across pages. Returns how many were new."""
        existing = self.items  # triggers hydration for restored datasets
        start_seq = len(existing)
        added: List[Dict[str, Any]] = []
        for item in items:
            if len(existing) >= max_items:
                break
            key = _dedupe_key(item)
            if key in self._seen:
                continue
            self._seen.add(key)
            existing.append(item)
            added.append(item)
        persistent_store.append_items(self.id, start_seq, added)
        return len(added)

    def info(self) -> DatasetInfo:
        return DatasetInfo(
            id=self.id,
            run_id=self.run_id,
            platform=self.platform,
            item_count=self.count,
            created_at=self.created_at,
        )


class RunStore:
    """Store for runs and their datasets (oldest evicted past the history
    limit). In-memory for speed, write-through to SQLite for durability —
    call load() at startup to hydrate what a previous process left behind."""

    def __init__(self):
        self.runs: "OrderedDict[str, RunInfo]" = OrderedDict()
        self.datasets: "OrderedDict[str, Dataset]" = OrderedDict()
        self.requests: Dict[str, RunRequest] = {}
        self.abort_flags: Dict[str, bool] = {}
        self._loaded = False

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
        dataset = Dataset(dataset_id, run_id, run.platform)
        self.datasets[dataset_id] = dataset
        self.requests[run_id] = req
        self.abort_flags[run_id] = False
        persistent_store.save_run(run.model_dump_json(), req.model_dump_json(), run_id, dataset_id)
        persistent_store.save_dataset(dataset_id, run_id, run.platform, dataset.created_at)
        self._prune()
        return run

    def persist(self, run: RunInfo) -> None:
        """Write a run's current state through to disk."""
        persistent_store.update_run(run.id, run.model_dump_json())

    def load(self) -> int:
        """Hydrate runs/datasets persisted by a previous process. Runs that
        were mid-flight when that process died are marked ABORTED — honest
        status beats a run stuck in RUNNING forever. Returns runs restored."""
        if self._loaded:
            return 0
        self._loaded = True
        restored = 0
        dataset_meta = {row[0]: row for row in persistent_store.load_datasets()}
        for run_json, req_json in persistent_store.load_runs():
            try:
                run = RunInfo.model_validate_json(run_json)
                req = RunRequest.model_validate_json(req_json)
            except Exception:
                continue  # never let one corrupt row break startup
            if run.id in self.runs:
                continue
            if run.status in ("READY", "RUNNING"):
                run.status = "ABORTED"
                run.status_detail = "Server restarted while this run was in flight."
                run.finished_at = _now_iso()
                persistent_store.update_run(run.id, run.model_dump_json())
            meta = dataset_meta.get(run.dataset_id)
            created_at = meta[3] if meta else run.started_at
            self.runs[run.id] = run
            self.datasets[run.dataset_id] = Dataset(
                run.dataset_id, run.id, run.platform, created_at=created_at, hydrated=False
            )
            self.requests[run.id] = req
            self.abort_flags[run.id] = False
            restored += 1
        self._prune()
        return restored

    def _prune(self):
        while len(self.runs) > settings.run_history_limit:
            old_id, old_run = self.runs.popitem(last=False)
            self.datasets.pop(old_run.dataset_id, None)
            self.requests.pop(old_id, None)
            self.abort_flags.pop(old_id, None)
            persistent_store.delete_run(old_id, old_run.dataset_id)

    def clear(self):
        self.runs.clear()
        self.datasets.clear()
        self.requests.clear()
        self.abort_flags.clear()
        persistent_store.wipe_runs()


run_store = RunStore()


async def fire_webhook(url: str, run: RunInfo) -> bool:
    """Best-effort run-finished webhook: one POST, bounded timeout, and a
    failed delivery never fails the run."""
    payload = {
        "event": "run.finished",
        "run": run.model_dump(exclude_none=True),
        "dataset_url": f"/api/v1/datasets/{run.dataset_id}/items",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.webhook_timeout) as client:
            resp = await client.post(url, json=payload)
        return resp.status_code < 400
    except Exception:
        return False


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
    run_store.persist(run)
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

            items = _items_from_response(resp)
            if req.clean:
                from app.services.ai_cleaner import tidy_item

                items = [tidy_item(i) for i in items]
            added = dataset.push(items, run.max_items)
            run.item_count = len(dataset.items)
            run_store.persist(run)  # crash mid-run keeps every page collected so far
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

    if req.clean and dataset.items:
        from app.services.ai_cleaner import summarize_items

        try:
            run.summary = await summarize_items(run.platform, run.identifier, dataset.items)
        except Exception:
            run.summary = None  # a failed summary never fails the run

    run.finished_at = _now_iso()
    run.duration_seconds = round(time.monotonic() - started, 2)
    run_store.persist(run)

    if req.webhook_url:
        await fire_webhook(req.webhook_url, run)
