import asyncio
import time


def _wait_job(client, job_id: int, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_system_status_and_config(client):
    status = client.get("/api/system/status").json()
    assert set(status) >= {"tools", "gpu", "nvenc", "unsloth", "checked_at"}
    config = client.get("/api/system/config").json()
    assert "projects_root" in config and "unsloth_base_url" in config


def test_project_create_validation(client, tmp_path):
    response = client.post(
        "/api/projects", json={"name": "X", "media_path": str(tmp_path / "missing")}
    )
    assert response.status_code == 400

    media = tmp_path / "m"
    media.mkdir()
    response = client.post("/api/projects", json={"name": "Trip", "media_path": str(media)})
    assert response.status_code == 200
    assert response.json()["slug"] == "trip"

    duplicate = client.post("/api/projects", json={"name": "trip", "media_path": str(media)})
    assert duplicate.status_code == 409


def test_scan_flow_end_to_end(client, media_dir):
    created = client.post(
        "/api/projects", json={"name": "Scan Test", "media_path": str(media_dir)}
    ).json()
    project_id = created["id"]
    assert created["scan_job_id"] > 0

    first_scan = _wait_job(client, created["scan_job_id"])
    assert first_scan["status"] == "done", first_scan

    rescan = client.post(f"/api/projects/{project_id}/scan")
    assert rescan.status_code == 202
    job = _wait_job(client, rescan.json()["job_id"])
    assert job["status"] == "done", job

    files = client.get(f"/api/projects/{project_id}/files").json()
    assert {f["rel_path"] for f in files} == {"a.mp4", "b.JPG", "sub/c.mov"}

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["files_total"] == 3
    assert detail["video_count"] == 2
    assert detail["photo_count"] == 1

    jobs = client.get("/api/jobs").json()
    assert any(j["kind"] == "scan" and j["status"] == "done" for j in jobs)

    deleted = client.delete(f"/api/projects/{project_id}?purge=true").json()
    assert deleted == {"deleted": True, "purged": True}
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_download_marks_reel_downloaded(client, media_dir):
    from app.core.config import get_settings
    from app.core.db import connect, migrate_project, project_db_path

    created = client.post(
        "/api/projects", json={"name": "DL Test", "media_path": str(media_dir)}
    ).json()
    project_id = created["id"]
    settings = get_settings()
    db_path = project_db_path(settings.projects_root, created["slug"])
    render_file = db_path.parent / "exports" / "reel.mp4"
    render_file.parent.mkdir(parents=True, exist_ok=True)
    render_file.write_bytes(b"fake mp4")

    async def _seed():
        conn = await connect(db_path)
        try:
            await migrate_project(conn)
            await conn.execute(
                "INSERT INTO edits(prompt, target_duration_sec, status, plan_json, model_id, "
                "render_path) VALUES ('p', 10, 'rendered', '{}', 'm', ?)",
                (str(render_file),),
            )
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(_seed())

    plans = client.get(f"/api/projects/{project_id}/plans").json()
    assert plans[0]["downloaded_at"] is None
    assert plans[0]["rendered_duration_sec"] is None

    response = client.get(f"/api/projects/{project_id}/plans/1/download")
    assert response.status_code == 200
    assert response.content == b"fake mp4"

    plans = client.get(f"/api/projects/{project_id}/plans").json()
    assert plans[0]["downloaded_at"] is not None
