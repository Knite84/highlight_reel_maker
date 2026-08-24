import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import files, jobs, media, plans, projects, scenes, system
from .core.config import get_settings
from .core.db import REGISTRY_SCHEMA, connect, migrate
from .core.logging import configure_logging
from .jobs.bus import EventBus
from .jobs.worker import JobRunner


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        os.environ.setdefault("HF_HOME", str(settings.hf_cache_dir))
        settings.ensure_dirs()
        registry = await connect(settings.registry_db_path)
        await migrate(registry, REGISTRY_SCHEMA)
        bus = EventBus()
        runner = JobRunner(registry, bus)
        runner.start()
        app.state.settings = settings
        app.state.registry = registry
        app.state.bus = bus
        app.state.runner = runner
        yield
        await runner.stop()
        await registry.close()

    app = FastAPI(title="ReelMaker", lifespan=lifespan)
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
        return {"ok": True}

    return app


app = create_app()
