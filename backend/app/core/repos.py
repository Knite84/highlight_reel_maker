from datetime import UTC, datetime
from typing import Any

import aiosqlite

JOB_COLUMNS = (
    "id, project_id, kind, status, progress, done, total, message, "
    "result_json, error, created_at, started_at, finished_at"
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


async def slug_exists(conn: aiosqlite.Connection, slug: str) -> bool:
    cur = await conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,))
    return await cur.fetchone() is not None


async def get_project_by_name(conn: aiosqlite.Connection, name: str) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM projects WHERE name = ?", (name,))
    return _dict(await cur.fetchone())


async def create_project(conn: aiosqlite.Connection, *, name: str, slug: str, media_path: str):
    cur = await conn.execute(
        "INSERT INTO projects(name, slug, media_path) VALUES (?, ?, ?) RETURNING *",
        (name, slug, media_path),
    )
    row = await cur.fetchone()
    await conn.commit()
    return row


async def get_project(conn: aiosqlite.Connection, project_id: int) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
    return _dict(await cur.fetchone())


async def list_projects(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
    cur = await conn.execute("SELECT * FROM projects ORDER BY id DESC")
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def delete_project(conn: aiosqlite.Connection, project_id: int) -> bool:
    cur = await conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    await conn.commit()
    return cur.rowcount > 0


async def update_project_counts(
    conn: aiosqlite.Connection, project_id: int, videos: int, photos: int
) -> None:
    await conn.execute(
        "UPDATE projects SET video_count = ?, photo_count = ?, "
        "scanned_at = ?, updated_at = ? WHERE id = ?",
        (videos, photos, utc_now(), utc_now(), project_id),
    )
    await conn.commit()


async def insert_job(
    conn: aiosqlite.Connection, *, project_id: int | None, kind: str, payload_json: str
) -> int:
    cur = await conn.execute(
        "INSERT INTO jobs(project_id, kind, payload_json) VALUES (?, ?, ?) RETURNING id",
        (project_id, kind, payload_json),
    )
    row = await cur.fetchone()
    await conn.commit()
    return int(row[0])


async def get_job(conn: aiosqlite.Connection, job_id: int) -> dict[str, Any] | None:
    cur = await conn.execute(f"SELECT {JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,))
    return _dict(await cur.fetchone())


async def list_jobs(conn: aiosqlite.Connection, limit: int = 50) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"SELECT {JOB_COLUMNS} FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_active_job_for(
    conn: aiosqlite.Connection, project_id: int, kind: str
) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"SELECT {JOB_COLUMNS} FROM jobs "
        "WHERE project_id = ? AND kind = ? AND status IN ('queued', 'running') "
        "ORDER BY id DESC LIMIT 1",
        (project_id, kind),
    )
    return _dict(await cur.fetchone())


async def update_job(
    conn: aiosqlite.Connection, job_id: int, *, fields: dict[str, Any]
) -> dict[str, Any] | None:
    assignments = ", ".join(f"{key} = ?" for key in fields)
    params = list(fields.values()) + [job_id]
    await conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", params)
    await conn.commit()
    return await get_job(conn, job_id)
