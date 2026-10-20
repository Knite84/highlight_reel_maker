import asyncio
import re
import shutil
from pathlib import Path
from uuid import uuid4

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core import db as coredb
from ..core import repos
from ..core.config import Settings
from ..core.paths import host_path, source_path
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


async def _unique_slug(registry, base: str, projects_root: Path) -> str:
    while True:
        slug = f"{base}-{uuid4().hex}"
        if not await repos.slug_exists(registry, slug) and not (projects_root / slug).exists():
            return slug


async def _create_project_storage(
    body: ProjectCreate,
    registry,
    settings: Settings,
):
    media_root = source_path(body.media_path, settings)
    if not media_root.is_dir():
        raise HTTPException(status_code=400, detail=f"Media path not found: {media_root}")
    if await repos.get_project_by_name(registry, body.name) is not None:
        raise HTTPException(status_code=409, detail="Project name already exists")
    slug = await _unique_slug(registry, _slugify(body.name), settings.projects_root)
    creation_conn = await coredb.connect(settings.registry_db_path)
    try:
        try:
            row = await repos.create_project(
                creation_conn, name=body.name, slug=slug, media_path=host_path(media_root, settings)
            )
        except aiosqlite.IntegrityError as exc:
            await creation_conn.rollback()
            raise HTTPException(status_code=409, detail="Project name already exists") from exc
    finally:
        await creation_conn.close()
    db_path = coredb.project_db_path(settings.projects_root, slug)
    conn = await coredb.connect(db_path)
    try:
        await coredb.migrate_project(conn)
    finally:
        await conn.close()
    return dict(row), media_root, db_path


@router.post("")
async def create_project(
    body: ProjectCreate,
    registry=Depends(get_registry),
    runner: JobRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
):
    # A collection cannot be registered inside storage while that storage is being purged.
    async with runner.project_mutation_lock:
        row, media_root, db_path = await _create_project_storage(body, registry, settings)

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


@router.get("/archived")
async def archived_projects(registry=Depends(get_registry)):
    return await repos.list_archived_projects(registry)


class ProjectRestore(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)


@router.post("/archived/{project_id}/restore")
async def restore_project(
    project_id: int,
    body: ProjectRestore,
    runner: JobRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
):
    async with runner.project_mutation_lock:
        conn = await coredb.connect(settings.registry_db_path)
        try:
            row = await repos.get_archived_project(conn, project_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Archived project not found")
            if not coredb.project_db_path(settings.projects_root, row["slug"]).is_file():
                raise HTTPException(status_code=409, detail="Archived knowledge database is missing")
            if body.name is not None:
                row["name"] = body.name
            row["updated_at"] = repos.utc_now()
            try:
                await repos.restore_project(conn, row)
            except aiosqlite.IntegrityError as exc:
                raise HTTPException(
                    status_code=409, detail="An active project has this name; restore with a new name"
                ) from exc
            return row
        finally:
            await conn.close()


async def _purge_archive(conn, project_id: int, settings: Settings) -> None:
    row = await repos.get_archived_project(conn, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Archived project not found")
    roots = [settings.projects_root]
    if settings.exports_root:
        roots.append(settings.exports_root)
    targets = []
    projects = await repos.list_projects(conn) + await repos.list_archived_projects(conn)
    for configured_root in roots:
        root = configured_root.resolve()
        target = (root / row["slug"]).resolve()
        if target == root or not target.is_relative_to(root):
            raise HTTPException(status_code=409, detail="Unsafe project storage path; purge refused")
        for project in projects:
            source = source_path(project["media_path"], settings)
            if source.is_relative_to(target) or target.is_relative_to(source):
                raise HTTPException(
                    status_code=409, detail="Project storage overlaps source media; purge refused"
                )
        if settings.registry_db_path.resolve().is_relative_to(target):
            raise HTTPException(status_code=409, detail="Project storage contains registry; purge refused")
        targets.append(target)
    try:
        for target in targets:
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, target)
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Purge incomplete: {exc}. Archive record retained; check storage and retry.",
        ) from exc
    await conn.execute("DELETE FROM jobs WHERE project_id=?", (project_id,))
    await conn.execute("DELETE FROM archived_projects WHERE id=?", (project_id,))
    await conn.commit()


@router.delete("/archived/{project_id}")
async def purge_archived_project(
    project_id: int,
    runner: JobRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
):
    async with runner.project_mutation_lock:
        conn = await coredb.connect(settings.registry_db_path)
        try:
            if await repos.get_active_project_job(conn, project_id) is not None:
                raise HTTPException(status_code=409, detail="Project has queued or running work")
            await _purge_archive(conn, project_id, settings)
        finally:
            await conn.close()
    return {"deleted": True, "purged": True}


@router.get("/{project_id}")
async def get_project(
    project_id: int,
    registry=Depends(get_registry),
    settings: Settings = Depends(get_settings_dep),
):
    row = await project_or_404(registry, project_id)
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
    runner: JobRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
):
    async with runner.project_mutation_lock:
        conn = await coredb.connect(settings.registry_db_path)
        try:
            row = await project_or_404(conn, project_id)
            if await repos.get_active_project_job(conn, project_id) is not None:
                raise HTTPException(
                    status_code=409, detail="Wait for queued/running work to finish before archiving"
                )
            await repos.archive_project(conn, row)
            if purge:
                # Preserve the old explicit purge API, with the same safety checks as archive purge.
                await _purge_archive(conn, project_id, settings)
                return {"deleted": True, "purged": True}
        finally:
            await conn.close()
    return {"deleted": True, "purged": False, "archived": True}


@router.post("/{project_id}/scan", status_code=202)
async def start_scan(
    project_id: int,
    registry=Depends(get_registry),
    runner: JobRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
):
    row = await project_or_404(registry, project_id)
    media_root = source_path(row["media_path"], settings)
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
    row = await project_or_404(registry, project_id)
    media_root = source_path(row["media_path"], settings)
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
