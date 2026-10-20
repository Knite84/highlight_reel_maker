from app.core.config import get_settings


def test_container_browses_only_shared_host_folders(client, media_dir):
    settings = get_settings()
    settings.container_mode = True
    settings.media_mounts = {r"Z:\Photos": str(media_dir)}
    settings.path_mappings = {r"Z:\Photos": str(media_dir), str(media_dir): str(media_dir)}
    assert client.post("/api/system/pick-folder").json()["browse"] is True
    roots = client.get("/api/system/folders").json()
    assert roots["folders"] == [{"name": r"Z:\Photos", "path": r"Z:\Photos"}]
    listing = client.get("/api/system/folders", params={"path": r"Z:\Photos"}).json()
    assert listing["path"] == r"Z:\Photos" and listing["parent"] is None
    assert listing["folders"] == [{"name": "sub", "path": r"Z:\Photos\sub"}]
    for forbidden in (r"Z:\Photos\..\private", r"C:\Windows", "/etc"):
        assert client.get("/api/system/folders", params={"path": forbidden}).status_code == 400
    created = client.post("/api/projects", json={"name": "Mapped", "media_path": r"Z:\Photos"})
    assert created.status_code == 200
    assert created.json()["media_path"] == r"Z:\Photos"


def test_remote_inference_configuration_is_rejected():
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="remote inference is disabled"):
        Settings(_env_file=None, unsloth_base_url="https://example.com/v1")
    assert Settings(
        _env_file=None, unsloth_base_url="http://host.docker.internal:8888"
    ).unsloth_base_url


def test_container_refuses_unmounted_persistent_storage(tmp_path):
    import pytest

    from app.core.config import Settings

    settings = Settings(
        _env_file=None, container_mode=True, data_root=tmp_path / "unmounted",
        projects_root=tmp_path / "projects", path_mappings={r"Z:\Data": "/data"},
    )
    with pytest.raises(RuntimeError, match="host bind mount"):
        settings.ensure_dirs()
    assert not settings.data_root.exists()
