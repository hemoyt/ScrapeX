"""Schedule routes — recurring dataset runs.

POST   /schedules                create a schedule ("scrape this every N minutes")
GET    /schedules                list schedules
GET    /schedules/{id}           one schedule
DELETE /schedules/{id}           delete a schedule
POST   /schedules/{id}/pause     stop firing (kept, resumable)
POST   /schedules/{id}/resume    start firing again
POST   /schedules/{id}/run       fire one run right now, off-cadence
"""
from fastapi import APIRouter, HTTPException, Query

from app.models import RunInfo, ScheduleInfo, ScheduleRequest
from app.routes.datasets import validate_run_target
from app.services.datasets import run_store
from app.services.scheduler import fire_schedule, schedule_store

router = APIRouter()


@router.post("/schedules", response_model=ScheduleInfo, status_code=201)
async def create_schedule(req: ScheduleRequest):
    validate_run_target(req.platform, req.query_type)
    schedule = schedule_store.create(req)
    if req.run_immediately and req.enabled:
        fire_schedule(schedule)
    return schedule


@router.get("/schedules", response_model=list[ScheduleInfo])
async def list_schedules(limit: int = Query(default=50, ge=1, le=200)):
    return list(reversed(list(schedule_store.schedules.values())))[:limit]


def _get_or_404(schedule_id: str) -> ScheduleInfo:
    schedule = schedule_store.schedules.get(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


@router.get("/schedules/{schedule_id}", response_model=ScheduleInfo)
async def get_schedule(schedule_id: str):
    return _get_or_404(schedule_id)


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str):
    _get_or_404(schedule_id)
    schedule_store.delete(schedule_id)


@router.post("/schedules/{schedule_id}/pause", response_model=ScheduleInfo)
async def pause_schedule(schedule_id: str):
    _get_or_404(schedule_id)
    return schedule_store.set_enabled(schedule_id, False)


@router.post("/schedules/{schedule_id}/resume", response_model=ScheduleInfo)
async def resume_schedule(schedule_id: str):
    _get_or_404(schedule_id)
    return schedule_store.set_enabled(schedule_id, True)


@router.post("/schedules/{schedule_id}/run", response_model=RunInfo, status_code=202)
async def run_schedule_now(schedule_id: str):
    """Fire one run immediately without touching the schedule's cadence."""
    schedule = _get_or_404(schedule_id)
    run_id = fire_schedule(schedule)
    return run_store.runs[run_id]
