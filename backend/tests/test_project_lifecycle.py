import asyncio
import json
import time

import pytest

from app.core.config import get_settings
from app.core.db import connect, project_db_path
from app.jobs.worker import ProjectUnavailable


def _create_idle(client, media, name="Trip"):
    response = client.post("/api/projects", json={"name": name, "media_path": str(media)})
    assert response.status_code == 200, response.text
    project = response.json()
    for _ in range(200):
        job = client.get(f"/api/jobs/{project['scan_job_id']}").json()
        if job["status"] == "done":
            return project
        assert job["status"] != "failed", job
        time.sleep(0.01)
    pytest.fail("scan did not finish")


def _registry_update(sql, params):
    async def run():
        conn = await connect(get_settings().registry_db_path)
        try:
            await conn.execute(sql, params)
            await conn.commit()
        finally:
            await conn.close()
    asyncio.run(run())


def test_archive_recreate_restore_keeps_original_knowledge(client, media_dir, tmp_path):
    old = _create_idle(client, media_dir)
    settings = get_settings()
    db = project_db_path(settings.projects_root, old["slug"])
    export = db.parent.parent / "exports" / "keep.mp4"
    export.parent.mkdir()
    export.write_bytes(b"last good export")

    async def seed():
        conn = await connect(db)
        try:
            await conn.execute("UPDATE files SET xxhash='expensive-analysis-identity'")
            await conn.execute(
                "INSERT INTO scenes(file_id,start_sec,end_sec) SELECT id,0,1 FROM files"
            )
            await conn.execute(
                "INSERT INTO scene_descriptions(scene_id,description,model_id) "
                "SELECT id,'retained analysis','local-model' FROM scenes"
            )
            await conn.execute(
                "INSERT INTO edits(prompt,target_duration_sec,plan_json,render_path,status) "
                "VALUES ('keep',10,'{}',?,'rendered')", (str(export),)
            )
            await conn.commit()
        finally:
            await conn.close()
    asyncio.run(seed())

    response = client.delete(f"/api/projects/{old['id']}")
    assert response.json() == {"deleted": True, "purged": False, "archived": True}
    assert client.get("/api/projects").json() == []
    assert client.get(f"/api/projects/{old['id']}").status_code == 404
    assert client.get("/api/projects/archived").json()[0]["slug"] == old["slug"]
    assert export.read_bytes() == b"last good export"

    other_media = tmp_path / "another-collection"
    other_media.mkdir()
    new = _create_idle(client, other_media)
    assert new["id"] != old["id"]
    assert new["slug"] != old["slug"]
    assert client.get(f"/api/projects/{new['id']}/files").json() == []
    assert client.get(f"/api/projects/{new['id']}/plans").json() == []
    restore_url = f"/api/projects/archived/{old['id']}/restore"
    assert client.post(restore_url, json={}).status_code == 409
    restored = client.post(restore_url, json={"name": "Restored trip"})
    assert restored.status_code == 200, restored.text
    assert restored.json()["id"] == old["id"]
    assert restored.json()["slug"] == old["slug"]
    assert len(client.get(f"/api/projects/{old['id']}/files").json()) == 3
    assert client.get(f"/api/projects/{old['id']}/plans").json()[0]["prompt"] == "keep"
    assert client.get("/api/projects/archived").json() == []

    async def verify_analysis():
        conn = await connect(db)
        try:
            rows = await (await conn.execute("SELECT description FROM scene_descriptions")).fetchall()
            assert [r[0] for r in rows] == ["retained analysis"] * 3
        finally:
            await conn.close()
    asyncio.run(verify_analysis())
    assert (media_dir / "a.mp4").is_file()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_archive_refuses_any_active_operation(client, media_dir, status):
    project = _create_idle(client, media_dir)
    _registry_update(
        "INSERT INTO jobs(project_id,kind,status) VALUES (?,'render',?)", (project["id"], status)
    )
    assert client.delete(f"/api/projects/{project['id']}").status_code == 409
    assert client.delete(f"/api/projects/{project['id']}?purge=true").status_code == 409
    assert client.get(f"/api/projects/{project['id']}").status_code == 200
    assert client.get("/api/projects/archived").json() == []


def test_purge_is_separate_and_preserves_source_media(client, media_dir):
    project = _create_idle(client, media_dir)
    storage = get_settings().projects_root / project["slug"]
    client.delete(f"/api/projects/{project['id']}")
    response = client.delete(f"/api/projects/archived/{project['id']}")
    assert response.json() == {"deleted": True, "purged": True}
    assert not storage.exists()
    assert (media_dir / "a.mp4").is_file()
    assert client.get("/api/projects/archived").json() == []
    assert client.post(f"/api/projects/archived/{project['id']}/restore", json={}).status_code == 404
    assert not any(j["project_id"] == project["id"] for j in client.get("/api/jobs").json())


def test_failed_purge_retains_recoverable_record(client, media_dir, monkeypatch):
    project = _create_idle(client, media_dir)
    client.delete(f"/api/projects/{project['id']}")

    def denied(path):
        raise PermissionError("locked export")

    monkeypatch.setattr("app.api.projects.shutil.rmtree", denied)
    response = client.delete(f"/api/projects/archived/{project['id']}")
    assert response.status_code == 409
    assert "Archive record retained" in response.json()["detail"]
    assert len(client.get("/api/projects/archived").json()) == 1
    assert client.post(f"/api/projects/archived/{project['id']}/restore", json={}).status_code == 200


@pytest.mark.parametrize("unsafe", ["root", "outside", "source"])
def test_purge_refuses_unsafe_or_source_overlapping_paths(client, media_dir, unsafe):
    project = _create_idle(client, media_dir)
    client.delete(f"/api/projects/{project['id']}")
    snapshot = dict(project)
    snapshot.pop("scan_job_id")
    if unsafe == "root":
        snapshot["slug"] = "."
    elif unsafe == "outside":
        snapshot["slug"] = ".."
    else:
        # Another project's source nested in the purge target must also be protected.
        source = get_settings().projects_root / project["slug"] / "originals"
        source.mkdir()
        (source / "precious.mp4").write_bytes(b"original")
        _registry_update(
            "INSERT INTO projects(name,slug,media_path) VALUES ('Other','other',?)", (str(source),)
        )
    _registry_update("UPDATE archived_projects SET project_json=? WHERE id=?",
                     (json.dumps(snapshot), project["id"]))
    response = client.delete(f"/api/projects/archived/{project['id']}")
    assert response.status_code == 409
    assert "purge refused" in response.json()["detail"]
    assert len(client.get("/api/projects/archived").json()) == 1


def test_stale_submission_cannot_enqueue_after_archive(client, media_dir):
    project = _create_idle(client, media_dir)
    runner = client.app.state.runner
    async def noop(ctx, payload):
        pytest.fail("archived work must not run")

    client.delete(f"/api/projects/{project['id']}")

    async def submit():
        return await runner.enqueue(project_id=project["id"], kind="analyze", handler=noop)

    with pytest.raises(ProjectUnavailable):
        client.portal.call(submit)
    assert not any(j["kind"] == "analyze" for j in client.get("/api/jobs").json())


def test_legacy_storage_is_not_reused(client, media_dir):
    legacy = get_settings().projects_root / "trip" / "cache"
    legacy.mkdir(parents=True)
    (legacy / "db.sqlite").write_bytes(b"retained legacy knowledge")
    project = _create_idle(client, media_dir)
    assert project["slug"] != "trip"
    assert (legacy / "db.sqlite").read_bytes() == b"retained legacy knowledge"


def test_archive_survives_application_restart(env, media_dir):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as first:
        project = _create_idle(first, media_dir)
        first.delete(f"/api/projects/{project['id']}")
    with TestClient(create_app()) as restarted:
        archive = restarted.get("/api/projects/archived").json()[0]
        assert archive["id"] == project["id"]
        response = restarted.post(f"/api/projects/archived/{project['id']}/restore", json={})
        assert response.status_code == 200
        assert response.json()["slug"] == project["slug"]
        assert len(restarted.get(f"/api/projects/{project['id']}/files").json()) == 3


def test_registry_upgrade_preserves_legacy_project_identity(env, media_dir):
    from fastapi.testclient import TestClient

    from app.core.db import REGISTRY_SCHEMA, migrate, migrate_project
    from app.main import create_app

    settings = get_settings()

    async def seed_old_registry():
        conn = await connect(settings.registry_db_path)
        try:
            old_schema = REGISTRY_SCHEMA.split("CREATE TABLE IF NOT EXISTS archived_projects")[0]
            await migrate(conn, old_schema)
            await conn.execute(
                "INSERT INTO projects(id,name,slug,media_path) VALUES (42,'Legacy','legacy-trip',?)",
                (str(media_dir),),
            )
            await conn.commit()
        finally:
            await conn.close()
        project_conn = await connect(project_db_path(settings.projects_root, "legacy-trip"))
        try:
            await migrate_project(project_conn)
        finally:
            await project_conn.close()

    asyncio.run(seed_old_registry())
    with TestClient(create_app()) as upgraded:
        assert upgraded.get("/api/projects").json()[0]["slug"] == "legacy-trip"
        assert upgraded.delete("/api/projects/42").status_code == 200
        restored = upgraded.post("/api/projects/archived/42/restore", json={})
        assert restored.status_code == 200
        assert restored.json()["id"] == 42 and restored.json()["slug"] == "legacy-trip"


def test_analysis_inventory_failure_is_visible_and_preserves_counts(client, media_dir, monkeypatch):
    from app.pipeline.scan import InventoryError

    project = _create_idle(client, media_dir)

    def disconnected(root):
        raise InventoryError("Cannot inventory disconnected drive; existing knowledge preserved")

    monkeypatch.setattr("app.pipeline.scan.walk_media", disconnected)
    response = client.post(f"/api/projects/{project['id']}/analyze")
    assert response.status_code == 202
    for _ in range(200):
        job = client.get(f"/api/jobs/{response.json()['job_id']}").json()
        if job["status"] == "failed":
            break
        time.sleep(0.01)
    assert job["status"] == "failed"
    assert "preserved" in job["error"]
    detail = client.get(f"/api/projects/{project['id']}").json()
    assert detail["files_total"] == 3
    assert detail["video_count"] == 2 and detail["photo_count"] == 1
