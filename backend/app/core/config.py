import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ReelMaker"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REELMAKER_", env_file=".env", extra="ignore")

    data_root: Path = _DEFAULT_ROOT
    projects_root: Path = _DEFAULT_ROOT / "Projects"
    unsloth_base_url: str = "http://127.0.0.1:8888"
    unsloth_api_key: str = ""
    planner_model_id: str = "unsloth/Qwen3-VL-8B-GGUF"
    path_mappings: dict[str, str] = {}
    media_mounts: dict[str, str] = {}
    container_mode: bool = False
    exports_root: Path | None = None

    @field_validator("unsloth_base_url")
    @classmethod
    def local_inference_only(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in ("http", "https")
            or url.hostname not in ("localhost", "127.0.0.1", "::1", "host.docker.internal")
            or url.username
            or url.password
        ):
            raise ValueError("Unsloth must be a local host service; remote inference is disabled")
        return value

    @property
    def registry_db_path(self) -> Path:
        return self.data_root / "registry.db"

    @property
    def hf_cache_dir(self) -> Path:
        return self.data_root / "hf"

    def ensure_dirs(self) -> None:
        if self.container_mode:
            if not self.path_mappings:
                raise RuntimeError("Configure Docker host folders before starting ReelMaker")
            for root in (self.data_root, self.projects_root, self.exports_root):
                if root is not None and not root.is_mount():
                    raise RuntimeError(f"Persistent folder must be a host bind mount: {root}")
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.hf_cache_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
