import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core import db as coredb
from ..core import repos
from ..core.config import Settings
from ..jobs.worker import JobRunner
from ..pipeline.analyze import analyze_job_handler
from ..pipeline.scan import scan_job_handler
from .deps import get_registry, get_runner, get_settings_dep, project_or_404

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    media_path: str = Field(min_length=1)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


async def _unique_slug(registry, base: str) -> str:
    slug = base
    counter = 1
    while await repos.slug_exists(registry, slug):
        counter += 1
        slug = f"{base}-{counter}"
    return slug


@router.post("")
async def create_project(
    body: ProjectCreate,
    registry=Depends(get_registry),
    runner: JobRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
):
    media_root = Path(body.media_path)
    if not media_root.is_dir():
        raise HTTPException(status_code=400, detail=f"Media path not found: {media_root}")
    if await repos.get_project_by_name(registry, body.name) is not None:
        raise HTTPException(status_code=409, detail="Project name already exists")
    slug = await _unique_slug(registry, _slugify(body.name))
    row = await repos.create_project(
        registry, name=body.name, slug=slug, media_path=str(media_root.resolve())
    )
    db_path = coredb.project_db_path(settings.projects_root, slug)
    conn = await coredb.connect(db_path)
    try:
        await coredb.migrate_project(conn)
    finally:
        await conn.close()

    async def on_done(result: dict) -> None:
        await repos.update_project_counts(
            registry, int(row["id"]), result["videos"], result["photos"]
        )

    job_id = await runner.enqueue(
        project_id=int(row["id"]),
        kind="scan",
        handler=scan_job_handler,
        payload={"media_path": str(media_root.resolve()), "db_path": str(db_path)},
        on_done=on_done,
    )
    project = dict(row)
    project["scan_job_id"] = job_id
    return project


@router.get("")
async def list_projects(registry=Depends(get_registry)):
    return await repos.list_projects(registry)


async def _project_or_404(registry, project_id: int):
    return await project_or_404(registry, project_id)


@router.get("/{project_id}")
async def get_project(
    project_id: int,
    registry=Depends(get_registry),
    settings: Settings = Depends(get_settings_dep),
):
    row = await _project_or_404(registry, project_id)
    data = dict(row)
    db_path = coredb.project_db_path(settings.projects_root, row["slug"])
    files_total = 0
    if db_path.exists():
        conn = await coredb.connect(db_path)
        try:
            cur = await conn.execute("SELECT COUNT(*) FROM files")
            files_total = int((await cur.fetchone())[0])
        finally:
            await conn.close()
    data["files_total"] = files_total
    return data


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    purge: bool = False,
    registry=Depends(get_registry),
    settings: Settings = Depends(get_settings_dep),
):
    row = await _project_or_404(registry, project_id)
    await repos.delete_project(registry, project_id)
    purged = False
    if purge:
        target = (settings.projects_root / row["slug"]).resolve()
        root = settings.projects_root.resolve()
        if target.is_relative_to(root):
            shutil.rmtree(target, ignore_errors=True)
            purged = True
    return {"deleted": True, "purged": purged}


@router.post("/{project_id}/scan", status_code=202)
async def start_scan(
    project_id: int,
    registry=Depends(get_registry),
    runner: JobRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
):
    row = await _project_or_404(registry, project_id)
    media_root = Path(row["media_path"])
    if not media_root.is_dir():
        raise HTTPException(status_code=400, detail=f"Media path not found: {media_root}")
    active = await repos.get_active_job_for(registry, project_id, "scan")
    if active is not None:
        raise HTTPException(status_code=409, detail="A scan is already queued or running")

    async def on_done(result: dict) -> None:
        await repos.update_project_counts(
            registry, project_id, result["videos"], result["photos"]
        )

    payload = {
        "media_path": str(media_root),
        "db_path": str(coredb.project_db_path(settings.projects_root, row["slug"])),
    }
    job_id = await runner.enqueue(
        project_id=project_id,
        kind="scan",
        handler=scan_job_handler,
        payload=payload,
        on_done=on_done,
    )
    return {"job_id": job_id}


@router.post("/{project_id}/analyze", status_code=202)
async def start_analysis(
    project_id: int,
    registry=Depends(get_registry),
    runner: JobRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
):
    row = await _project_or_404(registry, project_id)
    media_root = Path(row["media_path"])
    if not media_root.is_dir():
        raise HTTPException(status_code=400, detail=f"Media path not found: {media_root}")
    active = await repos.get_active_job_for(registry, project_id, "analyze")
    if active is not None:
        raise HTTPException(status_code=409, detail="An analysis is already queued or running")
    payload = {
        "media_path": str(media_root),
        "db_path": str(coredb.project_db_path(settings.projects_root, row["slug"])),
    }
    job_id = await runner.enqueue(
        project_id=project_id, kind="analyze", handler=analyze_job_handler, payload=payload
    )
    return {"job_id": job_id}
