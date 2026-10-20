import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .ai.providers import get_provider
from .api import files, jobs, media, plans, projects, scenes, system
from .core.config import get_settings
from .core.db import REGISTRY_SCHEMA, connect, migrate
from .core.logging import configure_logging
from .core.paths import UnsharedPath
from .jobs.bus import EventBus
from .jobs.recovery import recover_interrupted
from .jobs.worker import JobRunner, ProjectUnavailable, RunnerUnavailable


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        os.environ.setdefault("HF_HOME", str(settings.hf_cache_dir))
        settings.ensure_dirs()
        registry = await connect(settings.registry_db_path)
        await migrate(registry, REGISTRY_SCHEMA)
        await recover_interrupted(registry, settings)
        bus = EventBus()
        runner = JobRunner(registry, bus, queue_limit=settings.job_queue_limit)
        runner.start()
        app.state.settings = settings
        app.state.registry = registry
        app.state.bus = bus
        app.state.runner = runner
        try:
            yield
        finally:
            try:
                await runner.stop()
            finally:
                try:
                    await get_provider().close()
                finally:
                    await registry.close()

    app = FastAPI(title="ReelMaker", lifespan=lifespan)

    @app.exception_handler(ProjectUnavailable)
    async def project_unavailable(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(RunnerUnavailable)
    async def runner_unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)}, headers={"Retry-After": "3"})

    @app.exception_handler(UnsharedPath)
    async def unshared_path(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(system.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    app.include_router(scenes.router, prefix="/api")
    app.include_router(media.router, prefix="/api")
    app.include_router(plans.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")

    @app.get("/api/health")
    async def health():
        readiness = app.state.runner.readiness()
        if not readiness["ok"]:
            return JSONResponse(status_code=503, content={"ok": False, "worker": readiness})
        return {"ok": True}

    return app


app = create_app()
