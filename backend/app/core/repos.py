import json
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
    cur = await conn.execute(
        "SELECT 1 FROM projects WHERE slug=? UNION ALL "
        "SELECT 1 FROM archived_projects WHERE slug=? LIMIT 1", (slug, slug)
    )
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


async def get_active_project_job(conn: aiosqlite.Connection, project_id: int) -> aiosqlite.Row | None:
    cur = await conn.execute(
        "SELECT id FROM jobs WHERE project_id=? AND status IN ('queued', 'running') LIMIT 1",
        (project_id,),
    )
    return await cur.fetchone()


async def list_archived_projects(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
    cur = await conn.execute("SELECT project_json, archived_at FROM archived_projects ORDER BY id DESC")
    return [
        json.loads(row["project_json"]) | {"archived_at": row["archived_at"]}
        for row in await cur.fetchall()
    ]


async def get_archived_project(
    conn: aiosqlite.Connection, project_id: int
) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM archived_projects WHERE id=?", (project_id,))
    row = await cur.fetchone()
    return json.loads(row["project_json"]) if row else None


async def archive_project(conn: aiosqlite.Connection, project: dict[str, Any]) -> None:
    # Called with a dedicated registry connection: worker commits cannot split this transaction.
    try:
        await conn.execute(
            "INSERT INTO archived_projects(id,slug,project_json) VALUES (?,?,?)",
            (project["id"], project["slug"], json.dumps(project)),
        )
        await conn.execute("DELETE FROM projects WHERE id=?", (project["id"],))
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise


async def restore_project(conn: aiosqlite.Connection, project: dict[str, Any]) -> None:
    columns = (
        "id", "name", "slug", "media_path", "video_count", "photo_count",
        "scanned_at", "created_at", "updated_at",
    )
    try:
        await conn.execute(
            f"INSERT INTO projects({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [project[key] for key in columns],
        )
        await conn.execute("DELETE FROM archived_projects WHERE id=?", (project["id"],))
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise


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
