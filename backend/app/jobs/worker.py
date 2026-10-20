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


class ProjectUnavailable(Exception):
    pass


class RunnerUnavailable(Exception):
    pass


class QueueFull(RunnerUnavailable):
    pass


class JobFailedWithResult(RuntimeError):
    def __init__(self, message: str, result: dict) -> None:
        super().__init__(message)
        self.result = result


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
    def __init__(
        self, registry: aiosqlite.Connection, bus: EventBus, *, queue_limit: int = 32
    ) -> None:
        self._registry = registry
        self._bus = bus
        if queue_limit < 1:
            raise ValueError("queue_limit must be positive")
        self._queue: asyncio.Queue[int] = asyncio.Queue(maxsize=queue_limit)
        self._pending: dict[int, _Pending] = {}
        self._contexts: dict[int, JobContext] = {}
        self._task: asyncio.Task | None = None
        self._current: int | None = None
        # Serialize submission with archive/restore/purge within this single-worker app.
        self.project_mutation_lock = asyncio.Lock()
        self._handler_task: asyncio.Task | None = None
        self._accepting = True
        self._deferred: dict[int, dict[str, Any]] = {}
        self._storage_error: str | None = None
        self._admission_error: str | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            raise RuntimeError("Job worker is already running")
        self._task = asyncio.create_task(self._loop(), name="job-runner")
        self._task.add_done_callback(self._stopped)

    def _stopped(self, task: asyncio.Task) -> None:
        if self._accepting:
            logger.error(
                "Job worker stopped unexpectedly: %r",
                task.exception() if not task.cancelled() else "cancelled",
            )

    def readiness(self) -> dict:
        alive = self._task is not None and not self._task.done()
        return {
            "ok": self._accepting
            and alive
            and not self._storage_error
            and not self._admission_error
            and not self._deferred,
            "queued": self._queue.qsize(),
            "queue_limit": self._queue.maxsize,
            "error": self._admission_error
            or self._storage_error
            or (None if alive else "Job worker is not running; restart the app"),
        }

    async def check_admission(self, project_id: int | None) -> None:
        # Caller holds project_mutation_lock, through the durable insert and queue publication.
        if not self.readiness()["ok"]:
            raise RunnerUnavailable("Job worker unavailable; check app health/storage and retry")
        if self._queue.full():
            raise QueueFull("Job queue is full; wait for queued work to finish and retry")
        try:
            if project_id is not None:
                if await repos.get_project(self._registry, project_id) is None:
                    raise ProjectUnavailable(
                        "Project was archived or removed; refresh the project list"
                    )
                if await repos.get_active_project_job(self._registry, project_id):
                    raise ProjectUnavailable(
                        "This project already has queued or running work; wait or cancel it first"
                    )
        except aiosqlite.Error as exc:
            self._admission_error = str(exc)
            raise RunnerUnavailable(
                "Cannot verify job admission; check storage and restart the app"
            ) from exc

    async def stop(self) -> None:
        self._accepting = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Job worker failed; continuing resource cleanup")
        while not self._queue.empty():
            job_id = self._queue.get_nowait()
            self._pending.pop(job_id, None)
            await self._finish(
                job_id, status="cancelled", error="Application stopped before job started"
            )

    async def enqueue(
        self,
        *,
        project_id: int | None,
        kind: str,
        handler: Handler,
        payload: dict[str, Any] | None = None,
        on_done: OnDone | None = None,
    ) -> int:
        async with self.project_mutation_lock:
            return await self.enqueue_locked(project_id, kind, handler, payload, on_done)

    async def enqueue_locked(
        self,
        project_id: int | None,
        kind: str,
        handler: Handler,
        payload: dict[str, Any] | None,
        on_done: OnDone | None,
    ) -> int:
        """Caller must hold project_mutation_lock (also used for project creation)."""
        await self.check_admission(project_id)
        try:
            job_id = await repos.insert_job(
                self._registry,
                project_id=project_id,
                kind=kind,
                payload_json=json.dumps(payload or {}),
            )
            queued_row = await repos.get_job(self._registry, job_id)
        except Exception as exc:
            # A commit failure can be ambiguous: fail closed until restart/reconciliation.
            self._admission_error = str(exc)
            raise RunnerUnavailable(
                "Could not persist job admission; check storage and restart the app"
            ) from exc
        self._pending[job_id] = _Pending(handler=handler, payload=payload or {}, on_done=on_done)
        self._queue.put_nowait(job_id)
        # Initial queued state is already durable. SSE is advisory, not admission.
        if queued_row is not None:
            self._bus.publish({"type": "job", "job": queued_row})
        return job_id

    def cancel(self, job_id: int) -> bool:
        pending = self._pending.get(job_id)
        context = self._contexts.get(job_id)
        if pending is not None:
            pending.cancelled = True
        if context is not None:
            already_requested = context.cancelled()
            context.request_cancel()
            if self._handler_task is not None and not already_requested:
                self._handler_task.cancel()
        return pending is not None or context is not None

    async def wait_until_idle(self, timeout: float = 120.0) -> None:
        async def _poll() -> None:
            while self._queue.qsize() > 0 or self._current is not None or self._deferred:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_poll(), timeout)

    async def patch(self, job_id: int, **fields: Any) -> None:
        row = await repos.update_job(self._registry, job_id, fields=fields)
        if row is not None:
            self._bus.publish({"type": "job", "job": row})

    async def _loop(self) -> None:
        while True:
            if self._deferred:
                for deferred_id, fields in list(self._deferred.items()):
                    try:
                        await self.patch(deferred_id, **fields)
                    except Exception as exc:
                        logger.debug("Terminal persistence retry failed", exc_info=True)
                        self._storage_error = str(exc)
                        await asyncio.sleep(0.5)
                        break
                    else:
                        self._deferred.pop(deferred_id, None)
                if self._deferred:
                    continue
                self._storage_error = None
            job_id = await self._queue.get()
            self._current = job_id
            pending = self._pending.pop(job_id)
            try:
                if pending.cancelled:
                    await self._finish(job_id, status="cancelled")
                    continue
                context = JobContext(self, job_id)
                self._contexts[job_id] = context
                try:
                    await self.patch(job_id, status="running", started_at=repos.utc_now())
                    context.check_cancelled()
                    self._handler_task = asyncio.create_task(
                        pending.handler(context, pending.payload)
                    )
                    result = await self._handler_task
                    context.check_cancelled()
                    if pending.on_done is not None and result is not None:
                        await pending.on_done(result)
                    await self._finish(job_id, status="done", result_json=json.dumps(result or {}))
                except JobCancelled:
                    await self._finish(job_id, status="cancelled")
                except asyncio.CancelledError:
                    await self._finish(
                        job_id, status="cancelled", error="Operation interrupted/cancelled"
                    )
                    if not self._accepting:
                        raise
                except Exception as exc:
                    logger.exception("job %s failed", job_id)
                    await self._finish(
                        job_id,
                        status="failed",
                        error=str(exc),
                        result_json=json.dumps(exc.result)
                        if isinstance(exc, JobFailedWithResult)
                        else None,
                    )
                finally:
                    self._handler_task = None
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
        try:
            await self.patch(job_id, **fields)
        except Exception as exc:
            # Retry persistence, never the handler: it may already have published an artifact.
            logger.exception(
                "Job %s terminal state could not be saved; pausing admission/execution", job_id
            )
            self._storage_error = str(exc)
            self._deferred[job_id] = fields
