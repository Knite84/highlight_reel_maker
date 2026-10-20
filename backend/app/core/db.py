import sqlite3
from pathlib import Path
from uuid import uuid4

import aiosqlite

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    slug TEXT NOT NULL UNIQUE,
    media_path TEXT NOT NULL,
    video_count INTEGER NOT NULL DEFAULT 0,
    photo_count INTEGER NOT NULL DEFAULT 0,
    scanned_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    progress REAL NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    total INTEGER,
    message TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_project ON jobs(project_id, id DESC);
CREATE TABLE IF NOT EXISTS archived_projects (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    project_json TEXT NOT NULL,
    archived_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

PROJECT_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'photo')),
    size_bytes INTEGER NOT NULL,
    mtime REAL NOT NULL,
    xxhash TEXT,
    source_revision INTEGER NOT NULL DEFAULT 0,
    analysis_revision INTEGER NOT NULL DEFAULT 0,
    verified_at TEXT,
    duration_sec REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    codec TEXT,
    camera_model TEXT,
    gps_lat REAL,
    gps_lon REAL,
    captured_at TEXT,
    stage_version TEXT,
    model_id TEXT,
    error TEXT,
    discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_files_kind ON files(kind);
CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    rep_frame_path TEXT,
    stage_version TEXT,
    model_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (file_id, start_sec)
);
CREATE TABLE IF NOT EXISTS scene_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('primary', 'secondary')),
    path TEXT NOT NULL,
    timestamp_sec REAL,
    timestamp_source TEXT,
    blur REAL,
    brightness REAL,
    contrast REAL,
    motion REAL,
    stability REAL,
    dominant_colors TEXT
);
CREATE INDEX IF NOT EXISTS ix_scene_frames_scene ON scene_frames(scene_id);
CREATE TABLE IF NOT EXISTS scene_tags (
    scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    score REAL NOT NULL,
    PRIMARY KEY (scene_id, tag)
);
CREATE INDEX IF NOT EXISTS ix_scene_tags_tag ON scene_tags(tag);
CREATE TABLE IF NOT EXISTS embeddings (
    scene_id INTEGER PRIMARY KEY REFERENCES scenes(id) ON DELETE CASCADE,
    vector BLOB NOT NULL,
    model_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS scene_descriptions (
    scene_id INTEGER PRIMARY KEY REFERENCES scenes(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    objects TEXT NOT NULL DEFAULT '[]',
    actions TEXT NOT NULL DEFAULT '[]',
    camera_motion TEXT,
    people TEXT,
    emotion TEXT,
    scene_type TEXT,
    importance_score REAL,
    highlight_score REAL,
    model_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt TEXT NOT NULL,
    target_duration_sec REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'rendering', 'rendered', 'failed')),
    plan_json TEXT NOT NULL,
    model_id TEXT,
    render_path TEXT,
    stale_sources TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    rendered_at TEXT
);
CREATE TABLE IF NOT EXISTS analysis_stages (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    input_revision INTEGER NOT NULL,
    contract_version TEXT NOT NULL,
    model_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('legacy', 'success')),
    completed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(file_id, stage)
);
CREATE TABLE IF NOT EXISTS knowledge_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    archived_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


async def connect(path: Path) -> aiosqlite.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def migrate(conn: aiosqlite.Connection, schema: str) -> None:
    await conn.executescript(schema)
    await conn.commit()


async def _ensure_column(
    conn: aiosqlite.Connection, table: str, column: str, declaration: str
) -> None:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    if column not in {row[1] for row in rows}:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


async def migrate_project(conn: aiosqlite.Connection) -> None:
    columns = await (await conn.execute("PRAGMA table_info(files)")).fetchall()
    if columns and "source_revision" not in {column[1] for column in columns}:
        # SQLite's backup API includes committed WAL data. Never migrate a legacy
        # knowledge store without first producing a restorable local snapshot.
        database = await (await conn.execute("PRAGMA database_list")).fetchone()
        if database[2]:
            backup_path = Path(database[2]).with_name(
                f"db.before-source-provenance-{uuid4().hex}.sqlite"
            )
            backup = sqlite3.connect(backup_path, check_same_thread=False)
            try:
                await conn.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError(f"Pre-migration backup integrity failed: {backup_path}")
            finally:
                backup.close()
    await migrate(conn, PROJECT_SCHEMA)
    await _ensure_column(conn, "files", "rotation", "INTEGER")
    await _ensure_column(conn, "files", "analyzed_at", "TEXT")
    await _ensure_column(conn, "files", "source_revision", "INTEGER NOT NULL DEFAULT 0")
    await _ensure_column(conn, "files", "analysis_revision", "INTEGER NOT NULL DEFAULT 0")
    await _ensure_column(conn, "files", "verified_at", "TEXT")
    await _ensure_column(conn, "scene_frames", "timestamp_sec", "REAL")
    await _ensure_column(conn, "scene_frames", "timestamp_source", "TEXT")
    await _ensure_column(conn, "edits", "downloaded_at", "TEXT")
    await _ensure_column(conn, "edits", "rendered_duration_sec", "REAL")
    await _ensure_column(conn, "edits", "stale_sources", "TEXT NOT NULL DEFAULT '[]'")
    await conn.commit()


def project_db_path(projects_root: Path, slug: str) -> Path:
    return projects_root / slug / "cache" / "db.sqlite"
