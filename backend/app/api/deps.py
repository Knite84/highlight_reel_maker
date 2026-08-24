from fastapi import HTTPException, Request

from ..core import repos
from ..core.config import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_registry(request: Request):
    return request.app.state.registry


def get_runner(request: Request):
    return request.app.state.runner


def get_bus(request: Request):
    return request.app.state.bus


async def project_or_404(registry, project_id: int) -> dict:
    row = await repos.get_project(registry, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return row
