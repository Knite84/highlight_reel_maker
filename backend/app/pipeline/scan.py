import asyncio
import os
import stat as statlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.db import connect, migrate_project
from ..jobs.worker import JobCancelled

if TYPE_CHECKING:
    from ..jobs.worker import JobContext

VIDEO_EXTS = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mts", ".m2ts", ".ts", ".wmv", ".mpg", ".mpeg", ".3gp"}
)
PHOTO_EXTS = frozenset(
    {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp", ".avif", ".jxl"}
)
SKIP_DIRS = {"cache", "edits", "exports", "thumbnails", "frames"}

UPSERT_FILE = """
INSERT INTO files(rel_path, kind, size_bytes, mtime)
VALUES (?, ?, ?, ?)
ON CONFLICT(rel_path) DO UPDATE SET
    kind = excluded.kind,
    size_bytes = excluded.size_bytes,
    mtime = excluded.mtime,
    error = NULL
"""


def classify(suffix: str) -> str | None:
    suffix = suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in PHOTO_EXTS:
        return "photo"
    return None


class InventoryError(RuntimeError):
    """The inventory is incomplete; existing knowledge must not be reconciled away."""


def _root_identity(root: Path) -> tuple[int, int]:
    try:
        info = root.stat()
        if not statlib.S_ISDIR(info.st_mode):
            raise NotADirectoryError(str(root))
    except OSError as exc:
        raise InventoryError(
            f"Cannot inventory media folder {root}: {exc}. "
            "Existing indexed knowledge was preserved; reconnect/check access and retry."
        ) from exc
    return info.st_dev, info.st_ino


def walk_media(root: Path) -> list[tuple[str, str, int, float]]:
    identity = _root_identity(root)
    entries: list[tuple[str, str, int, float]] = []

    def onerror(exc: OSError) -> None:
        raise InventoryError(
            f"Incomplete media inventory at {exc.filename or root}: {exc}. "
            "Existing indexed knowledge was preserved; check access and retry."
        ) from exc

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS
        )
        for filename in filenames:
            path = Path(dirpath) / filename
            kind = classify(path.suffix.lower())
            if kind is None:
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                raise InventoryError(
                    f"Cannot inspect media file {path}: {exc}. "
                    "Existing indexed knowledge was preserved; check access and retry."
                ) from exc
            entries.append((path.relative_to(root).as_posix(), kind, stat.st_size, stat.st_mtime))
    if _root_identity(root) != identity:
        raise InventoryError("Media folder changed during inventory; preserved existing knowledge.")
    return entries


async def _remove_missing(conn: Any, seen: set[str]) -> int:
    cur = await conn.execute("SELECT id, rel_path FROM files")
    rows = await cur.fetchall()
    gone = [row["id"] for row in rows if row["rel_path"] not in seen]
    if gone:
        await conn.executemany("DELETE FROM files WHERE id = ?", [(i,) for i in gone])
    return len(gone)


async def scan_project(ctx: "JobContext", payload: dict[str, Any], conn: Any) -> dict[str, int]:
    root = Path(payload["media_path"])
    identity = await asyncio.to_thread(_root_identity, root)
    ctx.check_cancelled()
    entries = await asyncio.to_thread(walk_media, root)
    total = len(entries)
    await ctx.progress(done=0, total=total, message=f"Found {total} media files", force=True)
    counts = {"video": 0, "photo": 0}
    seen: set[str] = set()
    for idx, (rel_path, kind, size, mtime) in enumerate(entries, start=1):
        ctx.check_cancelled()
        await conn.execute(UPSERT_FILE, (rel_path, kind, size, mtime))
        seen.add(rel_path)
        counts[kind] += 1
        if idx % 50 == 0 or idx == total:
            await conn.commit()
            await ctx.progress(done=idx, force=True)
        else:
            await ctx.progress(done=idx)
    ctx.check_cancelled()
    if await asyncio.to_thread(_root_identity, root) != identity:
        raise InventoryError("Media folder changed before reconciliation; preserved existing knowledge.")
    removed = await _remove_missing(conn, seen)
    await conn.commit()
    return {"videos": counts["video"], "photos": counts["photo"], "removed": removed}


async def scan_job_handler(ctx: "JobContext", payload: dict[str, Any]) -> dict[str, int]:
    db_path = Path(payload["db_path"])
    conn = await connect(db_path)
    try:
        await migrate_project(conn)
        try:
            return await scan_project(ctx, payload, conn)
        except JobCancelled:
            await conn.commit()
            raise
    finally:
        await conn.close()
