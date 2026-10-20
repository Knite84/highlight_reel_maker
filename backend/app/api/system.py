import asyncio
import re
import shutil
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..core.config import Settings
from ..core.paths import UnsharedPath, host_path, source_path
from ..core.processes import run_process
from ..renderer.encode import resolve_encoder
from .deps import get_settings_dep

router = APIRouter(prefix="/system", tags=["system"])

_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0
_TTL_SECONDS = 15.0


async def _run_command(cmd: list[str]) -> str:
    try:
        result = await run_process(cmd, timeout=8)
        return result.stdout
    except (OSError, TimeoutError):
        return ""


async def _detect_nvenc() -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ok": False, "encoders": []}
    output = await _run_command([ffmpeg, "-hide_banner", "-encoders"])
    encoders = sorted(
        {
            parts[1]
            for line in output.splitlines()
            if "_nvenc" in line and len(parts := line.split()) > 2
        }
    )
    selected, _ = await resolve_encoder("proxy")
    return {"ok": selected == "h264_nvenc", "encoders": encoders, "selected": selected}


async def _detect_gpu() -> dict[str, Any]:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return {"ok": False, "name": None, "vram_gb": None}
    output = await _run_command(
        [smi, "--query-gpu=name,memory.total", "--format=csv,noheader"]
    )
    first_line = output.strip().splitlines()[0] if output.strip() else ""
    parts = [p.strip() for p in first_line.split(",")]
    name = parts[0] or None
    vram_gb = None
    if len(parts) > 1:
        match = re.search(r"\d+(?:\.\d+)?", parts[1])
        if match:
            vram_gb = round(float(match.group()) / 1024, 1)
    return {"ok": bool(name), "name": name, "vram_gb": vram_gb}


async def _check_unsloth(settings: Settings) -> dict[str, Any]:
    headers = {}
    if settings.unsloth_api_key:
        headers["Authorization"] = f"Bearer {settings.unsloth_api_key}"
    base = settings.unsloth_base_url.strip().rstrip("/")
    base = base.removesuffix("/v1")
    url = f"{base}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=4.0, trust_env=False) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json().get("data", [])
            models = [m.get("id") for m in data if m.get("id")]
            return {
                "ok": True,
                "url": base,
                "models": models,
                "error": None,
            }
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return {
            "ok": False,
            "url": base,
            "models": [],
            "error": str(exc),
        }


async def collect_status(settings: Settings) -> dict[str, Any]:
    tools = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe", "exiftool")}
    gpu, nvenc, unsloth = await asyncio.gather(
        _detect_gpu(), _detect_nvenc(), _check_unsloth(settings)
    )
    return {
        "tools": tools,
        "gpu": gpu,
        "nvenc": nvenc,
        "unsloth": unsloth,
        "planner_model_id": settings.planner_model_id,
        "checked_at": int(time.time()),
    }


@router.get("/status")
async def status(settings: Settings = Depends(get_settings_dep)) -> dict[str, Any]:
    global _CACHE, _CACHE_AT
    now = time.monotonic()
    if _CACHE is None or now - _CACHE_AT > _TTL_SECONDS:
        _CACHE = await collect_status(settings)
        _CACHE_AT = now
    return _CACHE


@router.post("/refresh")
async def refresh(settings: Settings = Depends(get_settings_dep)) -> dict[str, Any]:
    global _CACHE, _CACHE_AT
    _CACHE = await collect_status(settings)
    _CACHE_AT = time.monotonic()
    return _CACHE


@router.post("/pick-folder")
async def pick_folder(settings: Settings = Depends(get_settings_dep)) -> dict[str, Any]:
    if settings.container_mode:
        return {"path": None, "browse": True}
    def _dialog() -> str | None:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        try:
            path = filedialog.askdirectory(parent=root, title="Choose your media folder")
        finally:
            root.destroy()
        return path or None

    try:
        path = await asyncio.to_thread(_dialog)
    except Exception as exc:
        return {"path": None, "error": str(exc)}
    return {"path": path}


@router.get("/folders")
async def folders(path: str | None = None, settings: Settings = Depends(get_settings_dep)):
    if not settings.container_mode:
        raise HTTPException(status_code=400, detail="Use the Windows folder picker")
    if path is None:
        return {"path": None, "parent": None, "folders": [
            {"name": host, "path": host} for host in settings.media_mounts
        ]}
    current = source_path(path, settings)
    try:
        children = []
        for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir():
                try:
                    source_path(child, settings)
                except UnsharedPath:
                    continue
                children.append({"name": child.name, "path": host_path(child, settings)})
        parent = None
        try:
            source_path(current.parent, settings)
            parent = host_path(current.parent, settings)
        except UnsharedPath:
            pass
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot browse shared folder: {exc}") from exc
    return {"path": host_path(current, settings), "parent": parent, "folders": children}


@router.get("/config")
async def config(settings: Settings = Depends(get_settings_dep)) -> dict[str, Any]:
    return {
        "data_root": host_path(settings.data_root, settings),
        "projects_root": host_path(settings.projects_root, settings),
        "exports_root": host_path(settings.exports_root, settings) if settings.exports_root else None,
        "unsloth_base_url": settings.unsloth_base_url,
        "unsloth_api_key_set": bool(settings.unsloth_api_key),
        "planner_model_id": settings.planner_model_id,
    }
