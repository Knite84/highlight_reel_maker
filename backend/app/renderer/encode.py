import logging
import shutil

from ..core.processes import run_process

logger = logging.getLogger(__name__)

CANVASES = {"proxy": (1280, 720), "final": (1920, 1080)}
BITRATES = {"proxy": "8M", "final": "20M"}
VALID_PROFILES = tuple(CANVASES)


async def _encoders_output() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return ""
    try:
        result = await run_process([ffmpeg, "-hide_banner", "-encoders"], timeout=15)
        return result.stdout
    except (OSError, TimeoutError):
        return ""


async def resolve_encoder(profile: str) -> tuple[str, list[str]]:
    output = await _encoders_output()
    has_nvenc = "h264_nvenc" in output
    bitrate = BITRATES[profile]
    if has_nvenc:
        try:
            probe = await run_process([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi",
                "-i", "color=s=320x240:d=0.1", "-frames:v", "1", "-c:v", "h264_nvenc",
                "-f", "null", "-",
            ], timeout=15)
            if probe.returncode == 0:
                return "h264_nvenc", ["-b:v", bitrate, "-rc", "vbr", "-cq", "22", "-preset", "p5"]
            logger.warning("NVENC unavailable; using x264: %s", probe.stderr[-1000:])
        except (OSError, TimeoutError):
            logger.warning("NVENC probe failed/timed out; using x264")
    return "libx264", ["-b:v", bitrate, "-preset", "medium", "-crf", "19"]


def canvas_for(profile: str) -> tuple[int, int]:
    return CANVASES[profile]
