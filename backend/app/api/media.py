import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..core import db as coredb
from .deps import get_registry, get_settings_dep, project_or_404

router = APIRouter(prefix="/projects/{project_id}/media", tags=["media"])


@router.get("/{file_id}")
async def media_file(
    project_id: int,
    file_id: int,
    registry=Depends(get_registry),
    settings=Depends(get_settings_dep),
):
    project = await project_or_404(registry, project_id)
    db_path = coredb.project_db_path(settings.projects_root, project["slug"])
    media_root = Path(project["media_path"]).resolve()

    conn = await coredb.connect(db_path)
    try:
        cur = await conn.execute("SELECT rel_path FROM files WHERE id = ?", (file_id,))
        row = await cur.fetchone()
    finally:
        await conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    target = (media_root / row["rel_path"]).resolve()
    if not target.is_relative_to(media_root) or not target.is_file():
        raise HTTPException(status_code=404, detail="Media file missing on disk")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)
