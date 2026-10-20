import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REELMAKER_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("REELMAKER_PROJECTS_ROOT", str(tmp_path / "projects"))
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def media_dir(tmp_path):
    media = tmp_path / "media"
    (media / "sub").mkdir(parents=True)
    (media / "a.mp4").write_bytes(b"0" * 128)
    (media / "b.JPG").write_bytes(b"1" * 64)
    (media / "sub" / "c.mov").write_bytes(b"2" * 32)
    (media / "notes.txt").write_text("skip me")
    return media
