import asyncio
import re
import shutil
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends

from ..core.config import Settings
from .deps import get_settings_dep

router = APIRouter(prefix="/system", tags=["system"])

_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0
_TTL_SECONDS = 15.0


async def _run_command(cmd: list[str]) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return out.decode(errors="replace")
    except OSError:
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
    return {"ok": bool(encoders), "encoders": encoders}


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
        async with httpx.AsyncClient(timeout=4.0) as client:
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


@router.get("/config")
async def config(settings: Settings = Depends(get_settings_dep)) -> dict[str, Any]:
    return {
        "data_root": str(settings.data_root),
        "projects_root": str(settings.projects_root),
        "unsloth_base_url": settings.unsloth_base_url,
        "unsloth_api_key_set": bool(settings.unsloth_api_key),
        "planner_model_id": settings.planner_model_id,
    }
