import logging
import re

from ..core import repos
from ..core.config import Settings
from ..core.db import connect, project_db_path
from ..renderer.render import mark_edit_failed

logger = logging.getLogger(__name__)
INTERRUPTED = "Interrupted by application shutdown/restart. Retry the operation when ready."


async def recover_interrupted(registry, settings: Settings) -> None:
    # Handlers are not replayable after process loss. Reconcile artifacts before jobs.
    projects = await repos.list_projects(registry) + await repos.list_archived_projects(registry)
    for project in projects:
        path = project_db_path(settings.projects_root, project["slug"])
        if not path.is_file():
            continue
        conn = await connect(path)
        try:
            rows = await (
                await conn.execute("SELECT id FROM edits WHERE status='rendering'")
            ).fetchall()
        finally:
            await conn.close()
        for row in rows:
            await mark_edit_failed(path, row["id"], INTERRUPTED)
        exports = (
            settings.exports_root / project["slug"]
            if settings.exports_root
            else settings.projects_root / project["slug"] / "exports"
        )
        if exports.is_dir():
            for artifact in exports.iterdir():
                if re.fullmatch(r"\.[0-9a-f]{32}\.(partial\.mp4|title\.txt)", artifact.name):
                    try:
                        artifact.unlink()
                    except OSError:
                        logger.warning(
                            "Interrupted render temporary file could not be removed: %s", artifact
                        )
    await registry.execute(
        "UPDATE jobs SET status='failed',error=?,finished_at=? "
        "WHERE status IN ('queued','running')",
        (INTERRUPTED, repos.utc_now()),
    )
    await registry.commit()
