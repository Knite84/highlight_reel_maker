
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..core import db as coredb
from ..pipeline.search import list_tags as list_tags_query
from ..pipeline.search import search_scenes
from .deps import get_registry, get_settings_dep, project_or_404

router = APIRouter(prefix="/projects/{project_id}", tags=["scenes"])


async def _open_project_db(project_id: int, registry, settings):
    row = await project_or_404(registry, project_id)
    db_path = coredb.project_db_path(settings.projects_root, row["slug"])
    if not db_path.exists():
        return row, None
    conn = await coredb.connect(db_path)
    return row, conn


@router.get("/scenes/search")
async def scenes_search(
    project_id: int,
    q: str | None = None,
    kind: str | None = None,
    tag: str | None = None,
    limit: int = 40,
    registry=Depends(get_registry),
    settings=Depends(get_settings_dep),
):
    limit = max(1, min(limit, 100))
    _row, conn = await _open_project_db(project_id, registry, settings)
    if conn is None:
        return []
    try:
        return await search_scenes(conn, q, kind=kind, tag=tag, limit=limit)
    finally:
        await conn.close()


@router.get("/tags")
async def tags(
    project_id: int,
    registry=Depends(get_registry),
    settings=Depends(get_settings_dep),
):
    _row, conn = await _open_project_db(project_id, registry, settings)
    if conn is None:
        return []
    try:
        return await list_tags_query(conn)
    finally:
        await conn.close()


@router.get("/thumbs/{scene_id}")
async def thumb(
    project_id: int,
    scene_id: int,
    registry=Depends(get_registry),
    settings=Depends(get_settings_dep),
):
    row = await project_or_404(registry, project_id)
    cache_root = (settings.projects_root / row["slug"] / "cache").resolve()
    db_path = coredb.project_db_path(settings.projects_root, row["slug"])
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Scene not found")
    conn = await coredb.connect(db_path)
    try:
        cur = await conn.execute("SELECT rep_frame_path FROM scenes WHERE id = ?", (scene_id,))
        scene = await cur.fetchone()
    finally:
        await conn.close()
    if scene is None or not scene["rep_frame_path"]:
        raise HTTPException(status_code=404, detail="Scene has no thumbnail")
    target = (cache_root / scene["rep_frame_path"]).resolve()
    if not target.is_relative_to(cache_root) or not target.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail file missing")
    return FileResponse(target, media_type="image/jpeg")
