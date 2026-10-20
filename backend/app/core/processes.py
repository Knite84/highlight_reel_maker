import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


async def run_process(
    args: list[str],
    *,
    timeout: float = 600,
    cwd: Path | None = None,
    on_line: Callable[[str], Awaitable[None]] | None = None,
) -> ProcessResult:
    """Bound diagnostics, drain both pipes, and reap children on timeout/cancellation."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )

    async def drain(stream, callback=None, limit=64 * 1024):
        captured = bytearray()
        if callback:
            async for line in stream:
                captured.extend(line)
                del captured[:-limit]
                await callback(line.decode(errors="replace").strip())
        else:
            while chunk := await stream.read(8192):
                captured.extend(chunk)
                del captured[:-limit]
        return captured.decode(errors="replace")

    readers = [
        asyncio.create_task(drain(proc.stdout, on_line)),
        asyncio.create_task(drain(proc.stderr)),
    ]
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await asyncio.gather(*readers)
            code = await proc.wait()
        return ProcessResult(code, stdout, stderr)
    except BaseException:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await proc.wait()
        raise
