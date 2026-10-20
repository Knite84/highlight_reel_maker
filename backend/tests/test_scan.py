import asyncio
from pathlib import Path

import pytest

from app.core.db import PROJECT_SCHEMA, connect, migrate
from app.jobs.worker import JobCancelled
from app.pipeline.scan import InventoryError, classify, scan_project


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


def _seed_knowledge(media, db):
    _run_scan(FakeContext(), media, db)

    async def seed():
        conn = await connect(db)
        try:
            await conn.execute("UPDATE files SET xxhash='retained-hash'")
            await conn.execute(
                "INSERT INTO scenes(file_id,start_sec,end_sec) SELECT id,0,1 FROM files"
            )
            await conn.execute(
                "INSERT INTO embeddings(scene_id,vector,model_id) SELECT id,X'00','model' FROM scenes"
            )
            await conn.execute(
                "INSERT INTO scene_descriptions(scene_id,description,model_id) "
                "SELECT id,'expensive description','model' FROM scenes"
            )
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(seed())


def _knowledge_snapshot(db):
    async def read():
        conn = await connect(db)
        try:
            snapshot = {}
            for table in ("files", "scenes", "embeddings", "scene_descriptions"):
                cur = await conn.execute(f"SELECT * FROM {table} ORDER BY 1")
                snapshot[table] = [tuple(row) for row in await cur.fetchall()]
            return snapshot
        finally:
            await conn.close()

    return asyncio.run(read())


@pytest.mark.parametrize("failure", ["missing_root", "denied_subtree", "stat_failure"])
def test_incomplete_inventory_preserves_all_knowledge(tmp_path, media_dir, monkeypatch, failure):
    from app.pipeline import scan

    db = tmp_path / "knowledge.sqlite"
    _seed_knowledge(media_dir, db)
    before = _knowledge_snapshot(db)
    root = media_dir
    if failure == "missing_root":
        root = media_dir / "disconnected"
    elif failure == "denied_subtree":
        def partial_walk(root, *, onerror):
            yield str(root), ["sub"], ["a.mp4", "b.JPG"]
            onerror(PermissionError(13, "Access denied", str(root / "sub")))

        monkeypatch.setattr(scan.os, "walk", partial_walk)
    else:
        original_stat = Path.stat

        def failing_stat(path, *args, **kwargs):
            if path == media_dir / "sub" / "c.mov":
                raise PermissionError(13, "Access denied", str(path))
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", failing_stat)

    with pytest.raises(InventoryError, match="preserved"):
        _run_scan(FakeContext(), root, db)
    assert _knowledge_snapshot(db) == before


def test_root_disappears_after_inventory_preserves_descendants(tmp_path, media_dir):
    db = tmp_path / "knowledge.sqlite"
    _seed_knowledge(media_dir, db)
    before = _knowledge_snapshot(db)

    class DisconnectAfterWalk(FakeContext):
        async def progress(self, **kwargs):
            if kwargs.get("done") == 0:
                media_dir.rename(media_dir.with_name("unavailable"))

    with pytest.raises(InventoryError, match="preserved"):
        _run_scan(DisconnectAfterWalk(), media_dir, db)
    assert _knowledge_snapshot(db) == before


def test_cancel_before_reconciliation_does_not_remove_missing_knowledge(tmp_path, media_dir):
    db = tmp_path / "knowledge.sqlite"
    _seed_knowledge(media_dir, db)
    (media_dir / "b.JPG").unlink()
    before = _knowledge_snapshot(db)
    # One initial check and two surviving files, then cancellation at reconciliation.
    with pytest.raises(JobCancelled):
        _run_scan(CancelAfter(after=3), media_dir, db)
    assert _knowledge_snapshot(db) == before
