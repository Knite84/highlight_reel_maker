from fastapi import APIRouter, Depends, HTTPException

from ..core import db as coredb
from ..core import repos
from ..core.config import Settings
from .deps import get_registry, get_settings_dep

router = APIRouter(prefix="/projects/{project_id}/files", tags=["files"])

FILE_COLUMNS = (
    "id, rel_path, kind, size_bytes, mtime, duration_sec, error, analyzed_at, "
    "source_revision, analysis_revision, verified_at, "
    "CASE WHEN source_revision != analysis_revision THEN 'stale' "
    "WHEN EXISTS(SELECT 1 FROM analysis_stages a WHERE a.file_id=files.id AND a.status='success') "
    "THEN 'tracked' ELSE 'legacy' END AS provenance_status, "
    "(SELECT COUNT(*) FROM scenes s WHERE s.file_id = files.id) AS scene_count"
)


@router.get("")
async def list_files(
    project_id: int,
    kind: str | None = None,
    limit: int = 500,
    offset: int = 0,
    registry=Depends(get_registry),
    settings: Settings = Depends(get_settings_dep),
):
    limit = max(1, min(limit, 2000))
    offset = max(0, offset)
    row = await repos.get_project(registry, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    db_path = coredb.project_db_path(settings.projects_root, row["slug"])
    if not db_path.exists():
        return []
    conn = await coredb.connect(db_path)
    try:
        await coredb.migrate_project(conn)
        sql = f"SELECT {FILE_COLUMNS} FROM files"
        params: list = []
        if kind in ("video", "photo"):
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY rel_path LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await conn.close()
