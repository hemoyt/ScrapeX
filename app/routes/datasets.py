"""Dataset run routes — Apify-style background scraping jobs.

POST /runs                       start a run (paginate until max_items / budget)
GET  /runs                       list runs (newest first)
GET  /runs/{run_id}              run status
POST /runs/{run_id}/abort        request a cooperative abort
GET  /datasets/{dataset_id}        dataset metadata
GET  /datasets/{dataset_id}/items  page through items; format=json|ndjson|csv
"""
import csv
import io
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response

from app.models import DatasetInfo, DatasetItemsPage, RunInfo, RunRequest
from app.services.datasets import execute_run, run_store
from app.services.social_registry import get_platform, platform_names

router = APIRouter()


@router.post("/runs", response_model=RunInfo, status_code=202)
async def start_run(req: RunRequest, background_tasks: BackgroundTasks):
    """Start a dataset run. Poll GET /runs/{id}, then export the dataset."""
    try:
        svc = get_platform(req.platform)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown platform '{req.platform}'. Available: {platform_names()}",
        )
    from app.services.social_base import UNSUPPORTED

    if svc.capabilities.get(req.query_type, UNSUPPORTED) == UNSUPPORTED:
        supported = [qt.value for qt, c in svc.capabilities.items() if c != UNSUPPORTED]
        raise HTTPException(
            status_code=400,
            detail=f"{svc.name} does not support query_type={req.query_type.value}. Supported: {supported}",
        )

    run = run_store.create(req)
    background_tasks.add_task(execute_run, run.id)
    return run


@router.get("/runs", response_model=list[RunInfo])
async def list_runs(limit: int = Query(default=20, ge=1, le=100)):
    return list(reversed(list(run_store.runs.values())))[:limit]


@router.get("/runs/{run_id}", response_model=RunInfo)
async def get_run(run_id: str):
    run = run_store.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post("/runs/{run_id}/abort", response_model=RunInfo)
async def abort_run(run_id: str):
    run = run_store.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status in ("READY", "RUNNING"):
        run_store.abort_flags[run_id] = True
    return run


@router.get("/datasets/{dataset_id}", response_model=DatasetInfo)
async def get_dataset(dataset_id: str):
    ds = run_store.datasets.get(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return ds.info()


@router.get("/datasets/{dataset_id}/items")
async def get_dataset_items(
    dataset_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    format: str = Query(default="json", pattern="^(json|ndjson|csv)$"),
):
    """Page through a dataset. format=json returns an envelope with paging info;
    ndjson and csv return the selected slice as a downloadable body."""
    ds = run_store.datasets.get(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    page = ds.items[offset : offset + limit]

    if format == "ndjson":
        body = "\n".join(json.dumps(item, ensure_ascii=False, default=str) for item in page)
        return PlainTextResponse(body, media_type="application/x-ndjson")

    if format == "csv":
        return Response(
            _to_csv(page),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{dataset_id}.csv"'},
        )

    return DatasetItemsPage(
        dataset_id=dataset_id,
        total=len(ds.items),
        offset=offset,
        limit=limit,
        count=len(page),
        items=page,
    )


def _to_csv(items: list[dict]) -> str:
    """Flatten to CSV: union of top-level keys; nested values JSON-encoded."""
    if not items:
        return ""
    fields: list[str] = []
    for item in items:
        for key in item:
            if key not in fields:
                fields.append(key)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        writer.writerow({
            k: json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v
            for k, v in item.items()
        })
    return buf.getvalue()
