import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..core import repos
from .deps import get_bus, get_registry, get_runner

router = APIRouter(prefix="/jobs", tags=["jobs"])


async def _get_job_or_404(registry, job_id: int):
    row = await repos.get_job(registry, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


@router.get("")
async def list_jobs(limit: int = 50, registry=Depends(get_registry)):
    limit = max(1, min(limit, 200))
    return await repos.list_jobs(registry, limit=limit)


@router.get("/stream")
async def stream(bus=Depends(get_bus)):
    queue = bus.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{job_id}")
async def get_job(job_id: int, registry=Depends(get_registry)):
    return await _get_job_or_404(registry, job_id)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: int, registry=Depends(get_registry), runner=Depends(get_runner)):
    row = await _get_job_or_404(registry, job_id)
    handled = runner.cancel(job_id)
    if not handled and row["status"] in ("queued", "running"):
        await runner.patch(job_id, status="cancelled", finished_at=repos.utc_now())
        return await repos.get_job(registry, job_id)
    return await repos.get_job(registry, job_id)
