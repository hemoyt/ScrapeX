"""Recurring dataset runs — 'scrape this every N minutes', Apify-style.

A schedule is a stored RunRequest plus an interval. The scheduler loop
(started from the app's lifespan) wakes every few seconds, fires any
schedule whose next_run_at has passed by starting a normal dataset run,
and advances next_run_at by the interval. Schedules persist to SQLite,
so they survive restarts and missed slots simply fire on the next tick.
"""
import asyncio
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.config import settings
from app.models import RunRequest, ScheduleInfo, ScheduleRequest, SocialQueryType
from app.services.datasets import execute_run, run_store
from app.services.store import persistent_store


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class ScheduleStore:
    def __init__(self):
        self.schedules: "OrderedDict[str, ScheduleInfo]" = OrderedDict()
        self._loaded = False

    def create(self, req: ScheduleRequest) -> ScheduleInfo:
        now = _now()
        # First slot is one interval out; run_immediately is handled by the
        # route firing one run on top of this, so the cadence stays clean.
        first = now + timedelta(minutes=req.interval_minutes)
        schedule = ScheduleInfo(
            id=uuid.uuid4().hex[:12],
            name=req.name,
            platform=req.platform.lower(),
            query_type=req.query_type.value,
            identifier=req.identifier,
            interval_minutes=req.interval_minutes,
            max_items=req.max_items,
            options=req.options,
            clean=req.clean,
            webhook_url=req.webhook_url,
            enabled=req.enabled,
            created_at=_iso(now),
            next_run_at=_iso(first) if req.enabled else None,
        )
        self.schedules[schedule.id] = schedule
        self.persist(schedule)
        return schedule

    def persist(self, schedule: ScheduleInfo) -> None:
        persistent_store.save_schedule(schedule.id, schedule.model_dump_json())

    def load(self) -> int:
        if self._loaded:
            return 0
        self._loaded = True
        restored = 0
        for data_json in persistent_store.load_schedules():
            try:
                schedule = ScheduleInfo.model_validate_json(data_json)
            except Exception:
                continue  # never let one corrupt row break startup
            if schedule.id not in self.schedules:
                self.schedules[schedule.id] = schedule
                restored += 1
        return restored

    def delete(self, schedule_id: str) -> bool:
        found = self.schedules.pop(schedule_id, None) is not None
        persistent_store.delete_schedule(schedule_id)
        return found

    def set_enabled(self, schedule_id: str, enabled: bool) -> Optional[ScheduleInfo]:
        schedule = self.schedules.get(schedule_id)
        if schedule is None:
            return None
        schedule.enabled = enabled
        if enabled:
            schedule.next_run_at = _iso(_now() + timedelta(minutes=schedule.interval_minutes))
        else:
            schedule.next_run_at = None
        self.persist(schedule)
        return schedule

    def clear(self):
        self.schedules.clear()
        self._loaded = False
        persistent_store.wipe_schedules()


schedule_store = ScheduleStore()


def _to_run_request(schedule: ScheduleInfo) -> RunRequest:
    return RunRequest(
        platform=schedule.platform,
        query_type=SocialQueryType(schedule.query_type),
        identifier=schedule.identifier,
        max_items=schedule.max_items,
        options=schedule.options,
        clean=schedule.clean,
        webhook_url=schedule.webhook_url,
    )


def fire_schedule(schedule: ScheduleInfo) -> str:
    """Start one run for a schedule right now. Returns the run id."""
    run = run_store.create(_to_run_request(schedule))
    asyncio.create_task(execute_run(run.id))
    schedule.last_run_at = _iso(_now())
    schedule.last_run_id = run.id
    schedule.runs_started += 1
    schedule_store.persist(schedule)
    return run.id


async def tick(now: Optional[datetime] = None) -> List[str]:
    """Fire every schedule that's due. Returns the run ids started."""
    now = now or _now()
    started: List[str] = []
    for schedule in list(schedule_store.schedules.values()):
        if not schedule.enabled or not schedule.next_run_at:
            continue
        due_at = datetime.fromisoformat(schedule.next_run_at)
        if due_at > now:
            continue
        # Advance from *now*, not due_at: if the server slept through several
        # intervals we fire once and resume the cadence, not burst-fire.
        schedule.next_run_at = _iso(now + timedelta(minutes=schedule.interval_minutes))
        started.append(fire_schedule(schedule))
    return started


async def scheduler_loop() -> None:
    """Background task: check for due schedules every few seconds, forever.
    A single bad tick never kills the loop."""
    while True:
        try:
            await tick()
        except Exception:
            pass
        await asyncio.sleep(settings.scheduler_poll_interval)
