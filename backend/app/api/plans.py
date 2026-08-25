import json
import random
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..ai.providers import ProviderError, get_provider
from ..core import db as coredb
from ..core import repos
from ..core.config import Settings
from ..jobs.worker import JobRunner
from ..planner.generate import generate_plan
from ..renderer.encode import VALID_PROFILES
from ..renderer.render import render_job_handler
from .deps import get_registry, get_runner, get_settings_dep, project_or_404

router = APIRouter(prefix="/projects/{project_id}/plans", tags=["plans"])

EDIT_COLUMNS = (
    "id, prompt, target_duration_sec, status, model_id, render_path, error, created_at, "
    "rendered_duration_sec, downloaded_at"
)


class PlanCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    target_duration_sec: float = Field(default=45.0, gt=5, le=1800)
    seed: int | None = None


class RenderRequest(BaseModel):
    profile: str = "proxy"


async def _edit_or_404(conn, edit_id: int):
    cur = await conn.execute(f"SELECT {EDIT_COLUMNS}, plan_json FROM edits WHERE id = ?", (edit_id,))
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return dict(row)


async def plan_job_handler(ctx, payload: dict) -> dict:
    from app.planner.generate import PlannerInputError

    conn = await coredb.connect(Path(payload["db_path"]))
    try:
        await coredb.migrate_project(conn)
        provider = get_provider()
        settings = Settings()
        try:
            model = await provider.resolve_model(settings.planner_model_id)
        except ProviderError as exc:
            raise RuntimeError(
                f"LLM provider unreachable ({exc}); start Unsloth and retry"
            ) from exc
        await ctx.progress(done=0, total=3, message="Selecting candidate scenes", force=True)
        seed = int(payload.get("seed") or random.randrange(2**31))
        try:
            plan, refs = await generate_plan(
                provider,
                model,
                conn,
                prompt=payload["prompt"],
                target_duration_sec=float(payload["target_duration_sec"]),
                seed=seed,
            )
        except PlannerInputError as exc:
            raise RuntimeError(str(exc)) from exc
        except ProviderError as exc:
            detail = getattr(exc, "raw", "")
            raise RuntimeError(
                f"planning failed: {exc}"
                + (f" | model output started: {detail[:180]!r}" if detail else "")
            ) from exc
        await ctx.progress(done=2, total=3, message="Validating plan", force=True)
        cur = await conn.execute(
            "INSERT INTO edits(prompt, target_duration_sec, status, plan_json, model_id) "
            "VALUES (?, ?, 'planned', ?, ?) RETURNING id",
            (
                payload["prompt"],
                float(payload["target_duration_sec"]),
                plan.model_dump_json(),
                model,
            ),
        )
        edit_id = int((await cur.fetchone())[0])
        await conn.commit()
        await ctx.progress(done=3, total=3, message="Plan ready", force=True)
        return {
            "edit_id": edit_id,
            "clips": len(plan.clips),
            "clip_seconds": round(sum(c.end_sec - c.start_sec for c in plan.clips), 2),
            "candidates_considered": len(refs),
            "model": model,
            "seed": seed,
        }
    finally:
        await conn.close()


async def _project_context(project_id: int, registry, settings: Settings):
    project = await project_or_404(registry, project_id)
    db_path = coredb.project_db_path(settings.projects_root, project["slug"])
    media_root = Path(project["media_path"])
    export_dir = settings.projects_root / project["slug"] / "exports"
    return db_path, media_root, export_dir


@router.post("", status_code=202)
async def create_plan(
    project_id: int,
    body: PlanCreate,
    registry=Depends(get_registry),
    runner: JobRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
):
    db_path, _media_root, _export_dir = await _project_context(project_id, registry, settings)
    active = await repos.get_active_job_for(registry, project_id, "plan")
    if active is not None:
        raise HTTPException(status_code=409, detail="A planning job is already queued or running")
    payload = {
        "db_path": str(db_path),
        "prompt": body.prompt,
        "target_duration_sec": body.target_duration_sec,
        "seed": body.seed,
    }
    job_id = await runner.enqueue(
        project_id=project_id, kind="plan", handler=plan_job_handler, payload=payload
    )
    return {"job_id": job_id}


@router.get("")
async def list_plans(project_id: int, registry=Depends(get_registry), settings=Depends(get_settings_dep)):
    db_path, _m, _e = await _project_context(project_id, registry, settings)
    if not db_path.exists():
        return []
    conn = await coredb.connect(db_path)
    try:
        cur = await conn.execute(f"SELECT {EDIT_COLUMNS} FROM edits ORDER BY id DESC")
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()


@router.get("/{edit_id}")
async def get_plan(edit_id: int, project_id: int, registry=Depends(get_registry), settings=Depends(get_settings_dep)):
    db_path, _m, _e = await _project_context(project_id, registry, settings)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Plan not found")
    conn = await coredb.connect(db_path)
    try:
        row = await _edit_or_404(conn, edit_id)
        row["plan"] = json.loads(row.pop("plan_json"))
        return row
    finally:
        await conn.close()


@router.post("/{edit_id}/render", status_code=202)
async def render_plan(
    project_id: int,
    edit_id: int,
    body: RenderRequest,
    registry=Depends(get_registry),
    runner: JobRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
):
    if body.profile not in VALID_PROFILES:
        raise HTTPException(status_code=400, detail=f"profile must be one of {VALID_PROFILES}")
    db_path, media_root, export_dir = await _project_context(project_id, registry, settings)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Plan not found")
    conn = await coredb.connect(db_path)
    try:
        await _edit_or_404(conn, edit_id)
    finally:
        await conn.close()
    active = await repos.get_active_job_for(registry, project_id, "render")
    if active is not None:
        raise HTTPException(status_code=409, detail="A render job is already queued or running")
    payload = {
        "db_path": str(db_path),
        "media_root": str(media_root),
        "export_dir": str(export_dir),
        "edit_id": edit_id,
        "profile": body.profile,
    }
    job_id = await runner.enqueue(
        project_id=project_id, kind="render", handler=render_job_handler, payload=payload
    )
    return {"job_id": job_id}


@router.get("/{edit_id}/download")
async def download_render(edit_id: int, project_id: int, registry=Depends(get_registry), settings=Depends(get_settings_dep)):
    db_path, _m, _e = await _project_context(project_id, registry, settings)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Plan not found")
    conn = await coredb.connect(db_path)
    try:
        row = await _edit_or_404(conn, edit_id)
    finally:
        await conn.close()
    render_path = row.get("render_path")
    if row["status"] != "rendered" or not render_path or not Path(render_path).is_file():
        raise HTTPException(status_code=404, detail="Render not available yet")
    conn = await coredb.connect(db_path)
    try:
        await conn.execute(
            "UPDATE edits SET downloaded_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
            (edit_id,),
        )
        await conn.commit()
    finally:
        await conn.close()
    return FileResponse(Path(render_path), media_type="video/mp4", filename=f"reel_{edit_id}.mp4")
