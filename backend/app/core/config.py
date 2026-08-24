import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ReelMaker"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REELMAKER_", env_file=".env", extra="ignore")

    data_root: Path = _DEFAULT_ROOT
    projects_root: Path = _DEFAULT_ROOT / "Projects"
    unsloth_base_url: str = "http://127.0.0.1:8888"
    unsloth_api_key: str = ""
    planner_model_id: str = "unsloth/Qwen3-VL-8B-GGUF"

    @property
    def registry_db_path(self) -> Path:
        return self.data_root / "registry.db"

    @property
    def hf_cache_dir(self) -> Path:
        return self.data_root / "hf"

    def ensure_dirs(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.hf_cache_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
