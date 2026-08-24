import asyncio
import json
import math
import subprocess
from datetime import datetime
from pathlib import Path

import xxhash

_TIMEOUT_SECONDS = 600


def _run_sync(cmd: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.TimeoutExpired):
        return -1, "", "failed to run tool"


async def run_tool(cmd: list[str]) -> tuple[int, str, str]:
    return await asyncio.to_thread(_run_sync, cmd)


def xxh3_file_sync(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = xxhash.xxh3_128()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


async def checksum_file(path: Path) -> str:
    return await asyncio.to_thread(xxh3_file_sync, path)


def _parse_fps(value: str | None) -> float | None:
    if not value:
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return round(float(num) / den_f, 4) if den_f else None
        return round(float(value), 4)
    except ValueError:
        return None


def probe_media_sync(path: Path) -> dict:
    code, out, err = _run_sync(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if code != 0 or not out.strip():
        raise RuntimeError(f"ffprobe failed: {err.strip()[:300] or 'no output'}")
    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError("no video stream found")
    fmt = data.get("format", {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    color_transfer = (video.get("color_transfer") or "").lower()
    side_data_text = json.dumps(video.get("side_data_list") or []).lower()
    is_hdr = (
        color_transfer in {"smpte2084", "arib-std-b67", "hlg"}
        or "smpte2084" in side_data_text
        or "arib-std-b67" in side_data_text
    )
    duration = None
    for candidate in (video.get("duration"), fmt.get("duration")):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(side["rotation"]) % 360
    tags = video.get("tags") or {}
    raw_rotate = tags.get("rotate")
    if rotation == 0 and raw_rotate is not None:
        rotation = int(raw_rotate) % 360
    return {
        "duration_sec": duration,
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": _parse_fps(video.get("avg_frame_rate")),
        "codec": video.get("codec_name"),
        "camera_model": (fmt.get("tags") or {}).get("com.apple.quicktime.model")
        or (fmt.get("tags") or {}).get("model"),
        "gps_lat": None,
        "gps_lon": None,
        "captured_at": None,
        "rotation": rotation,
        "has_audio": has_audio,
        "is_hdr": is_hdr,
    }


def exiftool_metadata_sync(path: Path) -> dict:
    code, out, _err = _run_sync(
        [
            "exiftool",
            "-j",
            "-n",
            "-Model",
            "-DateTimeOriginal",
            "-CreateDate",
            "-GPSLatitude",
            "-GPSLongitude",
            str(path),
        ]
    )
    result: dict = {}
    if code != 0 or not out.strip():
        return result
    try:
        entries = json.loads(out)
    except json.JSONDecodeError:
        return result
    if not entries:
        return result
    entry = entries[0]
    result["camera_model"] = entry.get("Model")
    result["gps_lat"] = entry.get("GPSLatitude")
    result["gps_lon"] = entry.get("GPSLongitude")

    def normalize(ts: str | None) -> str | None:
        if not ts:
            return None
        text = ts.replace(":", "-", 2).replace(" ", "T", 1)
        try:
            datetime.fromisoformat(text)
            return text
        except ValueError:
            return None

    result["captured_at"] = normalize(entry.get("DateTimeOriginal")) or normalize(
        entry.get("CreateDate")
    )
    return result


async def extract_metadata(path: Path, kind: str) -> dict:
    if kind == "video":
        meta = await asyncio.to_thread(probe_media_sync, path)
    else:
        meta = {
            "duration_sec": None,
            "width": None,
            "height": None,
            "fps": None,
            "codec": None,
            "camera_model": None,
            "gps_lat": None,
            "gps_lon": None,
            "captured_at": None,
            "rotation": 0,
        }
    exif = await asyncio.to_thread(exiftool_metadata_sync, path)
    for key, value in exif.items():
        if value is None:
            continue
        if isinstance(value, float) and math.isnan(value):
            continue
        meta[key] = value
    if meta["width"] is None and meta["height"] is None and kind == "photo":
        from PIL import Image

        with Image.open(path) as img:
            meta["width"], meta["height"] = img.size
        exif_rotation = await asyncio.to_thread(_exif_orientation_sync, path)
        meta["rotation"] = exif_rotation
    return meta


def _exif_orientation_sync(path: Path) -> int:
    code, out, _err = _run_sync(["exiftool", "-j", "-n", "-Orientation", str(path)])
    if code != 0 or not out.strip():
        return 0
    try:
        entries = json.loads(out)
        orientation = entries[0].get("Orientation", 1) if entries else 1
    except (json.JSONDecodeError, IndexError):
        return 0
    mapping = {6: 90, 8: 270, 3: 180}
    return mapping.get(int(orientation), 0)
