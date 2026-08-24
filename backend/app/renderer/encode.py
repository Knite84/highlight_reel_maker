import asyncio
import shutil

CANVASES = {"proxy": (1280, 720), "final": (1920, 1080)}
BITRATES = {"proxy": "3M", "final": "10M"}
VALID_PROFILES = tuple(CANVASES)


async def _encoders_output() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-hide_banner",
            "-encoders",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return out.decode(errors="replace")
    except OSError:
        return ""


async def resolve_encoder(profile: str) -> tuple[str, list[str]]:
    output = await _encoders_output()
    has_nvenc = "_nvenc" in output
    bitrate = BITRATES[profile]
    if has_nvenc:
        encoder = "h264_nvenc"
        flags = ["-b:v", bitrate, "-rc", "vbr", "-cq", "27", "-preset", "p4"]
    else:
        encoder = "libx264"
        flags = ["-b:v", bitrate, "-preset", "veryfast", "-crf", "21"]
    return encoder, flags


def canvas_for(profile: str) -> tuple[int, int]:
    return CANVASES[profile]
