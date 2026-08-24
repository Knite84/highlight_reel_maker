import asyncio
from pathlib import Path

import pytest

from app.core.db import PROJECT_SCHEMA, connect, migrate
from app.jobs.worker import JobCancelled
from app.pipeline.scan import classify, scan_project


class FakeContext:
    def cancelled(self):
        return False

    def check_cancelled(self):
        return None

    async def progress(self, **kwargs):
        return None


class CancelAfter:
    def __init__(self, after: int):
        self.after = after
        self.calls = 0

    def cancelled(self):
        return True

    def check_cancelled(self):
        self.calls += 1
        if self.calls > self.after:
            raise JobCancelled

    async def progress(self, **kwargs):
        return None


def _run_scan(ctx, media_root: Path, db_path: Path):
    async def _inner():
        conn = await connect(db_path)
        try:
            await migrate(conn, PROJECT_SCHEMA)
            result = await scan_project(ctx, {"media_path": str(media_root)}, conn)
            cur = await conn.execute("SELECT COUNT(*) FROM files")
            total = int((await cur.fetchone())[0])
            return result, total
        finally:
            await conn.close()

    return asyncio.run(_inner())


def test_classify():
    assert classify(".mp4") == "video"
    assert classify(".MOV") == "video"
    assert classify(".jpg") == "photo"
    assert classify(".txt") is None
    assert classify("") is None


def test_scan_counts(tmp_path, media_dir):
    result, total = _run_scan(FakeContext(), media_dir, tmp_path / "db.sqlite")
    assert result == {"videos": 2, "photos": 1, "removed": 0}
    assert total == 3


def test_scan_upserts_and_removes_missing(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.mp4").write_bytes(b"x" * 10)
    (media / "b.jpg").write_bytes(b"y" * 5)
    db = tmp_path / "cache" / "db.sqlite"

    first, total_first = _run_scan(FakeContext(), media, db)
    assert first == {"videos": 1, "photos": 1, "removed": 0}
    assert total_first == 2

    (media / "b.jpg").unlink()
    second, _ = _run_scan(FakeContext(), media, db)
    assert second == {"videos": 1, "photos": 0, "removed": 1}


def test_scan_raises_when_cancelled(tmp_path, media_dir):
    ctx = CancelAfter(after=1)
    with pytest.raises(JobCancelled):
        _run_scan(ctx, media_dir, tmp_path / "db.sqlite")
