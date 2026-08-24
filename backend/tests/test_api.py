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
