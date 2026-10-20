"""Generate Compose host bindings without rewriting persisted projects or exposing keys."""

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings

REPO = Path(__file__).resolve().parents[2]


def registry_projects(path):
    if not path.is_file():
        return []
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        projects = [dict(row) for row in conn.execute("SELECT * FROM projects")]
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='archived_projects'").fetchone():
            projects.extend(
                json.loads(row[0])
                for row in conn.execute("SELECT project_json FROM archived_projects")
            )
        return projects


def backup_database(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as src,
        sqlite3.connect(destination) as dst,
    ):
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"Backup integrity check failed: {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media-root", action="append", default=[])
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--export-root")
    args = parser.parse_args()
    settings = Settings(_env_file=REPO / "backend" / ".env")
    projects = registry_projects(settings.registry_db_path)
    config_file = REPO / "compose.override.yaml"
    previous = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else {}
    old_env = previous.get("services", {}).get("app", {}).get("environment", {})
    # Keep already indexed removable/offline folders in the configuration. Compose refuses
    # a missing mount rather than silently inventing an empty folder and rescanning it.
    roots = {str(Path(p["media_path"]).resolve()) for p in projects}
    roots.update(json.loads(old_env.get("REELMAKER_MEDIA_MOUNTS", "{}")))
    for root in args.media_root:
        path = Path(root).resolve()
        if not path.is_dir():
            raise ValueError(f"Shared media folder not found: {path}")
        roots.add(str(path))
    minimal = []
    for root in sorted(roots, key=lambda p: (len(p), p.lower())):
        if not any(Path(root).is_relative_to(Path(parent)) for parent in minimal):
            minimal.append(root)
    if args.backup:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = REPO / ".docker-backups" / stamp
        if settings.registry_db_path.is_file():
            backup_database(settings.registry_db_path, destination / "registry.db")
        for project in projects:
            source = settings.projects_root / project["slug"] / "cache" / "db.sqlite"
            if source.is_file():
                backup_database(source, destination / project["slug"] / "cache" / "db.sqlite")
        print(f"Database backup (integrity checked): {destination}")
    settings.ensure_dirs()
    mappings = {
        str(settings.data_root.resolve()): "/data",
        str(settings.projects_root.resolve()): "/projects",
    }
    media = {root: f"/media/source-{i}" for i, root in enumerate(minimal, 1)}
    mappings.update(media)
    old_exports = next(
        (
            host
            for host, mounted in json.loads(old_env.get("REELMAKER_PATH_MAPPINGS", "{}")).items()
            if mounted == "/exports"
        ),
        None,
    )
    export_root = (
        Path(args.export_root or old_exports).resolve()
        if args.export_root or old_exports
        else settings.exports_root
    )
    if export_root:
        export_root.mkdir(parents=True, exist_ok=True)
        mappings[str(export_root.resolve())] = "/exports"
    volumes = []
    for host, target in mappings.items():
        volumes.append(
            {
                "type": "bind",
                "source": Path(host).as_posix(),
                "target": target,
                "read_only": target.startswith("/media/"),
                "bind": {"create_host_path": False},
            }
        )
    url = urlsplit(settings.unsloth_base_url)
    if url.hostname not in ("localhost", "127.0.0.1", "::1", "host.docker.internal"):
        raise ValueError("Unsloth must run on this host; remote inference URLs are not supported")
    host_url = urlunsplit((url.scheme, f"host.docker.internal:{url.port or 80}", url.path, "", ""))
    override = {
        "services": {
            "app": {
                "volumes": volumes,
                "environment": {
                    "REELMAKER_PATH_MAPPINGS": json.dumps(mappings),
                    "REELMAKER_MEDIA_MOUNTS": json.dumps(media),
                    "REELMAKER_UNSLOTH_BASE_URL": host_url,
                },
            }
        }
    }
    if export_root:
        override["services"]["app"]["environment"]["REELMAKER_EXPORTS_ROOT"] = "/exports"
    temporary = config_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(override, indent=2), encoding="utf-8")
    temporary.replace(config_file)
    print("Configured host-backed data/projects and read-only media folders:")
    for root in media:
        print(f"  {root}")
    print("Unsloth remains external. No source media or analysis artifacts were copied.")


if __name__ == "__main__":
    main()
