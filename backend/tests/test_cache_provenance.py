import asyncio
import json
import os
from collections import Counter

import numpy as np
import pytest
from PIL import Image
from test_pipeline import FakeContext

from app.ai.providers import ProviderError
from app.core.db import connect, migrate_project
from app.jobs.worker import JobFailedWithResult
from app.pipeline import analyze, frames_cv, provenance
from app.pipeline.scan import scan_project
from app.planner.candidates import select_candidates


@pytest.fixture
def library(tmp_path, monkeypatch, env):
    media = tmp_path / "media"
    media.mkdir()
    for name in ("a", "b"):
        Image.new("RGB", (80, 40), (80, 40, 20)).save(media / f"{name}.jpg")
    db = tmp_path / "project" / "cache" / "db.sqlite"
    calls = Counter()

    async def metadata(path, kind):
        calls["metadata"] += 1
        return {
            "duration_sec": None,
            "width": 80,
            "height": 40,
            "fps": None,
            "codec": None,
            "camera_model": None,
            "gps_lat": None,
            "gps_lon": None,
            "captured_at": None,
            "rotation": 0,
        }

    class Embedding:
        def encode_images(self, paths):
            calls["embedded"] += len(paths)
            return np.array([[1.0, 0.0] for _ in paths], dtype=np.float32)

    class Provider:
        fail = False

        async def resolve_model(self, *args, **kwargs):
            calls["resolve"] += 1
            return "fake-vision"

        def build_vision_prompt(self, prompt, paths):
            return prompt

        async def chat_json(self, **kwargs):
            calls["described"] += 1
            if self.fail:
                raise ProviderError("offline")
            return {"description": "cached expensive knowledge"}

    provider = Provider()
    monkeypatch.setattr(analyze, "extract_metadata", metadata)
    monkeypatch.setattr(analyze.Embedder, "get", lambda: Embedding())
    monkeypatch.setattr(analyze, "score_tags", lambda vector: [("trail", 0.8)])
    monkeypatch.setattr(analyze, "get_provider", lambda: provider)

    def run(**options):
        return asyncio.run(
            analyze.analyze_job_handler(
                FakeContext(),
                {
                    "media_path": str(media),
                    "db_path": str(db),
                    **options,
                },
            )
        )

    def query(sql, params=()):
        async def execute():
            conn = await connect(db)
            try:
                rows = await (await conn.execute(sql, params)).fetchall()
                await conn.commit()
                return [dict(row) for row in rows]
            finally:
                await conn.close()

        return asyncio.run(execute())

    return media, db, calls, provider, run, query


def test_unchanged_analysis_and_touch_reuse_inference(library):
    media, _db, calls, _provider, run, query = library
    run()
    before = calls.copy()
    run()
    assert calls == before  # Includes resolve_model: fully cached analysis works offline.
    path = media / "a.jpg"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    run()
    assert calls == before
    assert all(r["source_revision"] == r["analysis_revision"] for r in query("SELECT * FROM files"))


def test_replacement_only_rebuilds_changed_source_and_archives_knowledge(library):
    media, db, _calls, _provider, run, query = library
    run()
    old = query("SELECT * FROM scene_frames ORDER BY scene_id")
    other = query("SELECT * FROM scene_descriptions WHERE scene_id=2")
    Image.new("RGB", (100, 60), "blue").save(media / "a.jpg")
    result = run()
    assert result["metadata"] == 1
    assert result["visuals"] == result["embedded"] == result["described"] == 1
    assert query("SELECT * FROM scene_descriptions WHERE scene_id=2") == other
    history = query("SELECT * FROM knowledge_history")
    snapshot = json.loads(history[-1]["snapshot_json"])
    assert snapshot["scene_descriptions"][0]["description"] == "cached expensive knowledge"
    assert snapshot["embeddings"][0]["vector"]["base64"]
    assert all((db.parent / row["path"]).is_file() for row in old)


def test_deep_verify_detects_preserved_size_and_mtime(library):
    media, _db, calls, _provider, run, _query = library
    run()
    source = media / "a.jpg"
    stat = source.stat()
    # JPEG readers permit trailing bytes: alter content without breaking decoding.
    original = source.read_bytes()
    # Alter a JPEG comment-independent byte: append padding once and establish baseline.
    source.write_bytes(original + b"aaaa")
    run()
    stat = source.stat()
    baseline = calls.copy()
    source.write_bytes(original + b"bbbb")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    run()
    assert calls == baseline  # Quick inventory's documented limit.
    result = run(deep_verify=True)
    assert result["metadata"] == 1
    assert calls["described"] == baseline["described"] + 1


def test_missing_thumbnail_repairs_only_visuals_without_model_calls(library):
    _media, db, calls, _provider, run, query = library
    run()
    before = calls.copy()
    scene = query("SELECT * FROM scenes ORDER BY id")[0]
    (db.parent / scene["rep_frame_path"]).unlink()
    result = run()
    assert result["visuals"] == 1
    assert calls == before
    current = query("SELECT * FROM scenes WHERE id=?", (scene["id"],))[0]
    assert (db.parent / current["rep_frame_path"]).is_file()


@pytest.mark.parametrize("stage", ["embeddings", "descriptions", "visuals"])
def test_contract_change_invalidates_only_descendants(library, monkeypatch, stage):
    _media, _db, calls, _provider, run, query = library
    run()
    before = calls.copy()
    scenes = query("SELECT id FROM scenes ORDER BY id")
    monkeypatch.setitem(provenance.CONTRACTS, stage, "test-upgrade.v2")
    result = run()
    assert result["metadata"] == 0
    assert query("SELECT id FROM scenes ORDER BY id") == scenes
    assert calls["metadata"] == before["metadata"]
    if stage == "descriptions":
        assert calls["embedded"] == before["embedded"]
    if stage == "embeddings":
        assert calls["described"] == before["described"]
    if stage == "visuals":
        assert result["visuals"] == 2
        assert result["embedded"] == result["described"] == 2
    assert query("SELECT * FROM knowledge_history")


def test_legacy_migration_preserves_rows_without_inference(library):
    _media, _db, calls, _provider, run, query = library
    run()
    descriptions = query("SELECT * FROM scene_descriptions ORDER BY scene_id")
    query("DELETE FROM analysis_stages")
    query("UPDATE files SET source_revision=0,analysis_revision=0")
    before = calls.copy()
    run()
    assert calls == before
    assert query("SELECT * FROM scene_descriptions ORDER BY scene_id") == descriptions
    assert {r["status"] for r in query("SELECT * FROM analysis_stages WHERE stage != 'orientation'")} == {"legacy"}
    assert query("SELECT status FROM analysis_stages WHERE stage='orientation'")[0]["status"] == "success"
    from app.api.files import FILE_COLUMNS

    assert {row["provenance_status"] for row in query(f"SELECT {FILE_COLUMNS} FROM files")} == {"legacy"}


def test_failed_frame_write_is_retryable_and_not_published(library, monkeypatch):
    _media, _db, calls, _provider, run, query = library
    original = frames_cv.cv2.imwrite
    monkeypatch.setattr(frames_cv.cv2, "imwrite", lambda *args: False)
    with pytest.raises(JobFailedWithResult, match="Failed to write frame"):
        run()
    assert not query("SELECT * FROM scene_frames")
    assert calls["embedded"] == calls["described"] == 0
    monkeypatch.setattr(frames_cv.cv2, "imwrite", original)
    result = run()
    assert result["embedded"] == result["described"] == 2


def test_source_mutating_during_decode_never_publishes_visuals(library, monkeypatch):
    _media, _db, _calls, _provider, run, query = library
    original = analyze.process_photo_async

    async def mutate(path, *args):
        result = await original(path, *args)
        path.write_bytes(path.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(analyze, "process_photo_async", mutate)
    with pytest.raises(JobFailedWithResult, match="Source changed"):
        run()
    assert not query("SELECT * FROM scene_frames")
    assert all(r["analysis_revision"] != r["source_revision"] for r in query("SELECT * FROM files"))


def test_retry_provider_failure_preserves_successful_stages(library):
    _media, _db, calls, provider, run, _query = library
    provider.fail = True
    with pytest.raises(JobFailedWithResult):
        run()
    before = calls.copy()
    provider.fail = False
    result = run()
    assert result["metadata"] == result["visuals"] == result["embedded"] == 0
    assert calls["metadata"] == before["metadata"]
    assert result["described"] == 2


def test_coverage_distinguishes_cached_knowledge_from_work_this_run(library):
    _media, _db, _calls, _provider, run, _query = library
    first = run()
    reused = run()
    assert reused["embedded"] == reused["described"] == 0
    assert first["coverage"] == reused["coverage"] == {
        "files_total": 2, "files_current": 2, "files_failed": 0,
        "scenes_current": 2, "scenes_embedded": 2, "scenes_described": 2,
    }
    assert reused["inventory"] == {"videos": 0, "photos": 2, "removed": 0}


@pytest.mark.parametrize("failure", ["ml_missing", "provider_offline", "invalid_response", "damaged_file"])
def test_incomplete_analysis_retains_coverage_errors_and_retry_guidance(library, monkeypatch, failure):
    from app.ai.providers import PlannerOutputError

    media, _db, _calls, provider, run, _query = library
    if failure == "ml_missing":
        def missing():
            raise ImportError("missing ML dependencies")
        monkeypatch.setattr(analyze.Embedder, "get", missing)
    elif failure == "provider_offline":
        provider.fail = True
    elif failure == "invalid_response":
        async def invalid(**kwargs):
            raise PlannerOutputError("invalid response", raw="not JSON")
        monkeypatch.setattr(provider, "chat_json", invalid)
    else:
        (media / "b.jpg").write_bytes(b"damaged photo")
    with pytest.raises(JobFailedWithResult) as raised:
        run()
    result = raised.value.result
    assert result["errors"]
    assert result["inventory"]["photos"] == result["coverage"]["files_total"] == 2
    assert "retry Analyze" in str(raised.value)
    if failure == "ml_missing":
        assert result["coverage"]["scenes_embedded"] == 0
        assert result["coverage"]["scenes_described"] == 2
    elif failure in ("provider_offline", "invalid_response"):
        assert result["coverage"]["scenes_embedded"] == 2
        assert result["coverage"]["scenes_described"] == 0
    else:
        assert result["coverage"]["files_failed"] == 1
        assert any(error["file"] == "b.jpg" for error in result["errors"])


def test_changed_sources_are_excluded_until_refreshed(library):
    media, db, _calls, _provider, run, _query = library
    run()
    Image.new("RGB", (100, 60), "red").save(media / "a.jpg")

    async def check():
        conn = await connect(db)
        try:
            await scan_project(FakeContext(), {"media_path": str(media)}, conn)
            candidates = await select_candidates(conn)
            assert [c["rel_path"] for c in candidates] == ["b.jpg"]
        finally:
            await conn.close()

    asyncio.run(check())


def test_additive_migration_can_be_restored_on_disposable_backup(library):
    import sqlite3

    _media, db, _calls, _provider, run, query = library
    run()
    original = query("SELECT * FROM scene_descriptions ORDER BY scene_id")
    backup = db.with_name("backup.sqlite")
    with sqlite3.connect(db) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    query("DELETE FROM scene_descriptions")
    with sqlite3.connect(backup) as src, sqlite3.connect(db) as dst:
        src.backup(dst)

    async def migrate_again():
        conn = await connect(db)
        try:
            await migrate_project(conn)
        finally:
            await conn.close()

    asyncio.run(migrate_again())
    assert query("SELECT * FROM scene_descriptions ORDER BY scene_id") == original


def test_legacy_schema_gets_integrity_checked_backup_before_migration(library):
    import sqlite3

    _media, db, _calls, _provider, run, query = library
    run()
    original = query("SELECT * FROM scene_descriptions ORDER BY scene_id")
    for column in ("source_revision", "analysis_revision", "verified_at"):
        query(f"ALTER TABLE files DROP COLUMN {column}")
    query("ALTER TABLE edits DROP COLUMN stale_sources")

    async def migrate_old():
        conn = await connect(db)
        try:
            await migrate_project(conn)
        finally:
            await conn.close()

    asyncio.run(migrate_old())
    backups = list(db.parent.glob("db.before-source-provenance-*.sqlite"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "source_revision" not in {r[1] for r in backup.execute("PRAGMA table_info(files)")}
    assert query("SELECT * FROM scene_descriptions ORDER BY scene_id") == original


def test_changed_plan_stays_blocked_after_reanalysis_and_later_touch(library):
    media, _db, _calls, _provider, run, query = library
    run()
    query(
        "INSERT INTO edits(prompt,target_duration_sec,plan_json) VALUES('old',5,?)",
        (json.dumps({"clips": [{"rel_path": "a.jpg"}]}),),
    )
    Image.new("RGB", (100, 60), "blue").save(media / "a.jpg")
    run()
    stale = query("SELECT stale_sources FROM edits")[0]["stale_sources"]
    assert "a.jpg" in json.loads(stale)
    stat = (media / "a.jpg").stat()
    os.utime(media / "a.jpg", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    run()
    assert query("SELECT stale_sources FROM edits")[0]["stale_sources"] == stale


def test_identical_source_touch_does_not_invalidate_existing_plan(library):
    media, _db, _calls, _provider, run, query = library
    run()
    query(
        "INSERT INTO edits(prompt,target_duration_sec,plan_json) VALUES('old',5,?)",
        (json.dumps({"clips": [{"rel_path": "a.jpg"}]}),),
    )
    stat = (media / "a.jpg").stat()
    os.utime(media / "a.jpg", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    run()
    assert not json.loads(query("SELECT stale_sources FROM edits")[0]["stale_sources"])


def test_render_verifies_content_even_without_rescan(library):
    from app.renderer.render import _verify_sources

    media, db, _calls, _provider, run, query = library
    run()
    source = media / "a.jpg"
    expected = query("SELECT xxhash FROM files WHERE rel_path='a.jpg'")[0]["xxhash"]
    query(
        "INSERT INTO edits(prompt,target_duration_sec,plan_json) VALUES('old',5,?)",
        (json.dumps({"clips": [{"rel_path": "a.jpg"}]}),),
    )
    source.write_bytes(source.read_bytes() + b"new content")
    with pytest.raises(RuntimeError, match="Plan source content changed"):
        asyncio.run(_verify_sources(db, {"a.jpg": source}, {"a.jpg": expected}))
    assert json.loads(query("SELECT stale_sources FROM edits")[0]["stale_sources"])
    row = query("SELECT * FROM files WHERE rel_path='a.jpg'")[0]
    assert row["source_revision"] != row["analysis_revision"]


@pytest.mark.parametrize("model", ["embeddings", "descriptions"])
def test_model_change_preserves_other_stages(library, monkeypatch, model):
    _media, _db, calls, _provider, run, _query = library
    run()
    before = calls.copy()
    if model == "embeddings":
        monkeypatch.setattr(analyze, "MODEL_ID", "new-siglip-model")
    else:
        monkeypatch.setattr(analyze.get_settings(), "planner_model_id", "new-vision-model")
    result = run()
    assert result["metadata"] == result["visuals"] == 0
    if model == "embeddings":
        assert result["embedded"] == 2
        assert calls["described"] == before["described"]
    else:
        assert result["described"] == 2
        assert calls["embedded"] == before["embedded"]


def test_orientation_upgrade_rebuilds_only_affected_visual_descendants(library):
    media, db, calls, _provider, run, query = library
    image = Image.new("RGB", (80, 40), "blue")
    exif = image.getexif()
    exif[274] = 6
    image.save(media / "a.jpg", exif=exif)
    run()
    before = calls.copy()
    scenes = query("SELECT id,file_id FROM scenes ORDER BY id")
    old_frames = query("SELECT path FROM scene_frames")
    # Simulate a retained cache made before the explicit orientation contract.
    query("DELETE FROM analysis_stages WHERE stage='orientation' AND file_id=1")
    query("UPDATE scenes SET rep_frame_path=NULL WHERE file_id=1")
    query("UPDATE files SET rotation=90 WHERE id=1")
    result = run()
    assert result["metadata"] == result["scenes"] == 0
    assert result["visuals"] == result["embedded"] == result["described"] == 1
    assert calls["metadata"] == before["metadata"]
    assert query("SELECT id,file_id FROM scenes ORDER BY id") == scenes
    assert all((db.parent / frame["path"]).is_file() for frame in old_frames)
    assert query("SELECT reason FROM knowledge_history")[-1]["reason"] == "Display orientation contract upgraded"
    row = query("SELECT width,height,rotation FROM files WHERE id=1")[0]
    assert row == {"width": 40, "height": 80, "rotation": 0}
