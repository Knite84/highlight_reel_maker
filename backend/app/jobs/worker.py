import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiosqlite

from ..core import repos
from .bus import EventBus

logger = logging.getLogger(__name__)

Handler = Callable[["JobContext", dict[str, Any]], Awaitable[dict[str, Any] | None]]
OnDone = Callable[[dict[str, Any]], Awaitable[None]]


class JobCancelled(Exception):
    pass


@dataclass
class _Pending:
    handler: Handler
    payload: dict[str, Any]
    on_done: OnDone | None = None
    cancelled: bool = False


class JobContext:
    def __init__(self, runner: "JobRunner", job_id: int) -> None:
        self.job_id = job_id
        self.total: int | None = None
        self.done = 0
        self.message = ""
        self._runner = runner
        self._cancel = asyncio.Event()
        self._last_emit = 0.0

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def request_cancel(self) -> None:
        self._cancel.set()

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise JobCancelled

    async def progress(
        self,
        *,
        advance: int = 0,
        done: int | None = None,
        total: int | None = None,
        message: str | None = None,
        force: bool = False,
    ) -> None:
        self.done = done if done is not None else self.done + advance
        if total is not None:
            self.total = total
        if message is not None:
            self.message = message
        now = time.monotonic()
        if force or now - self._last_emit >= 0.2:
            self._last_emit = now
            await self._flush()

    async def _flush(self) -> None:
        fraction = round(self.done / self.total, 4) if self.total else 0.0
        await self._runner.patch(
            self.job_id,
            progress=fraction,
            done=self.done,
            total=self.total,
            message=self.message or None,
        )


class JobRunner:
    def __init__(self, registry: aiosqlite.Connection, bus: EventBus) -> None:
        self._registry = registry
        self._bus = bus
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._pending: dict[int, _Pending] = {}
        self._contexts: dict[int, JobContext] = {}
        self._task: asyncio.Task | None = None
        self._current: int | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="job-runner")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def enqueue(
        self,
        *,
        project_id: int | None,
        kind: str,
        handler: Handler,
        payload: dict[str, Any] | None = None,
        on_done: OnDone | None = None,
    ) -> int:
        job_id = await repos.insert_job(
            self._registry,
            project_id=project_id,
            kind=kind,
            payload_json=json.dumps(payload or {}),
        )
        self._pending[job_id] = _Pending(handler=handler, payload=payload or {}, on_done=on_done)
        await self._queue.put(job_id)
        await self.patch(job_id, status="queued")
        return job_id

    def cancel(self, job_id: int) -> bool:
        pending = self._pending.get(job_id)
        context = self._contexts.get(job_id)
        if pending is not None:
            pending.cancelled = True
        if context is not None:
            context.request_cancel()
        return pending is not None or context is not None

    async def wait_until_idle(self, timeout: float = 120.0) -> None:
        async def _poll() -> None:
            while self._queue.qsize() > 0 or self._current is not None:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_poll(), timeout)

    async def patch(self, job_id: int, **fields: Any) -> None:
        row = await repos.update_job(self._registry, job_id, fields=fields)
        if row is not None:
            self._bus.publish({"type": "job", "job": row})

    async def _loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            self._current = job_id
            pending = self._pending.pop(job_id)
            try:
                if pending.cancelled:
                    await self._finish(job_id, status="cancelled")
                    continue
                await self.patch(job_id, status="running", started_at=repos.utc_now())
                context = JobContext(self, job_id)
                self._contexts[job_id] = context
                try:
                    result = await pending.handler(context, pending.payload)
                    if context.cancelled():
                        await self._finish(job_id, status="cancelled")
                        continue
                    await self._finish(job_id, status="done", result_json=json.dumps(result or {}))
                    if pending.on_done is not None and result is not None:
                        try:
                            await pending.on_done(result)
                        except Exception:
                            logger.exception("on_done callback failed for job %s", job_id)
                except JobCancelled:
                    await self._finish(job_id, status="cancelled")
                except asyncio.CancelledError:
                    await self._finish(job_id, status="cancelled")
                    raise
                except Exception as exc:
                    logger.exception("job %s failed", job_id)
                    await self._finish(job_id, status="failed", error=str(exc))
                finally:
                    self._contexts.pop(job_id, None)
            finally:
                self._current = None

    async def _finish(
        self,
        job_id: int,
        *,
        status: str,
        result_json: str | None = None,
        error: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {"status": status, "finished_at": repos.utc_now()}
        if result_json is not None:
            fields["result_json"] = result_json
        if error is not None:
            fields["error"] = error
        await self.patch(job_id, **fields)
