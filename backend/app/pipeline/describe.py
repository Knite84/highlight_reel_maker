import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..ai.providers import LLMProvider, PlannerOutputError

DESCRIBE_SYSTEM = (
    "You are a precise video scene analyst for a local media library. "
    "You will see representative frames from one detected scene of a home video. "
    "Judge only what is visible. Be concrete and concise. "
    "Respond ONLY with a JSON object matching the requested schema."
)

PROMPT_TEMPLATE = (
    "Analyze this scene (span {start:.1f}s to {end:.1f}s in its source clip). "
    "The frames are shown in chronological order.\n\n"
    "Return JSON with exactly these keys:\n"
    '- "description": one or two sentences describing what happens\n'
    '- "objects": array of up to 8 notable objects/nouns visible\n'
    '- "actions": array of up to 4 actions occurring\n'
    '- "camera_motion": one of "static", "handheld", "pan", "tilt", "tracking", '
    '"zoom", "drone", "unknown"\n'
    '- "people": short string describing people present ("none" if no people)\n'
    '- "emotion": one of "neutral", "calm", "joyful", "excited", "tense", "somber"\n'
    '- "scene_type": short label such as "hiking trail", "beach", "city street", '
    '"indoor gathering"\n'
    '- "importance_score": float 0-1, how central this content likely is to a memory reel\n'
    '- "highlight_score": float 0-1, visual/energetic highlight potential'
)


class SceneDescription(BaseModel):
    description: str = Field(min_length=1)
    objects: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    camera_motion: str = "unknown"
    people: str = "none"
    emotion: str = "neutral"
    scene_type: str = "general"
    importance_score: float = Field(default=0.5, ge=0, le=1)
    highlight_score: float = Field(default=0.5, ge=0, le=1)

    @classmethod
    def coerce(cls, data: dict[str, Any]) -> "SceneDescription":
        def _str_list(value: Any) -> list[str]:
            if isinstance(value, list):
                return [str(item)[:80] for item in value][:8]
            if isinstance(value, str) and value.strip():
                return [value.strip()][:8]
            return []

        def _score(value: Any, default: float = 0.5) -> float:
            try:
                return max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                return default

        raw_objects = data.get("objects")
        return cls(
            description=str(data.get("description") or "").strip()[:600] or "Unspecified scene",
            objects=_str_list(raw_objects),
            actions=_str_list(data.get("actions")),
            camera_motion=str(data.get("camera_motion") or "unknown")[:24],
            people=str(data.get("people") or "none")[:120],
            emotion=str(data.get("emotion") or "neutral")[:24],
            scene_type=str(data.get("scene_type") or "general")[:60],
            importance_score=_score(data.get("importance_score")),
            highlight_score=_score(data.get("highlight_score")),
        )


def frame_paths_for_scene(cache_dir: Path, primary_rel: str | None, secondary_rels: list[str]) -> list[Path]:
    paths: list[Path] = []
    if primary_rel:
        candidate = cache_dir / primary_rel
        if candidate.is_file():
            paths.append(candidate)
    for rel in secondary_rels[:2]:
        candidate = cache_dir / rel
        if candidate.is_file():
            paths.append(candidate)
    return paths


async def describe_pending_scenes(
    conn: Any,
    provider: LLMProvider,
    model: str,
    cache_dir: Path,
    *,
    file_id: int,
    max_scenes: int | None = None,
) -> dict[str, int]:
    cur = await conn.execute(
        "SELECT s.id, s.start_sec, s.end_sec, "
        "MAX(CASE WHEN sf.role='primary' THEN sf.path END) AS primary_path, "
        "GROUP_CONCAT(CASE WHEN sf.role='secondary' THEN sf.path END) AS secondary_paths "
        "FROM scenes s "
        "JOIN scene_frames sf ON sf.scene_id = s.id "
        "WHERE s.file_id = ? AND NOT EXISTS ("
        "  SELECT 1 FROM scene_descriptions d WHERE d.scene_id = s.id"
        ") GROUP BY s.id ORDER BY s.start_sec",
        (file_id,),
    )
    rows = await cur.fetchall()
    if not rows:
        return {"described": 0, "failed": 0}
    described = 0
    failed = 0
    secondary_cache: dict[int, list[str]] = {}
    cur2 = await conn.execute(
        "SELECT scene_id, path FROM scene_frames WHERE role = 'secondary' AND scene_id IN "
        f"({','.join('?' * len(rows))})",
        [row["id"] for row in rows],
    )
    for sec_row in await cur2.fetchall():
        secondary_cache.setdefault(int(sec_row["scene_id"]), []).append(sec_row["path"])

    for row in rows:
        if max_scenes is not None and described >= max_scenes:
            break
        image_paths = frame_paths_for_scene(
            cache_dir, row["primary_path"], secondary_cache.get(int(row["id"]), [])
        )
        if not image_paths:
            failed += 1
            continue
        prompt_text = PROMPT_TEMPLATE.format(start=row["start_sec"], end=row["end_sec"])
        parts = provider.build_vision_prompt(prompt_text, image_paths)
        try:
            data = await provider.chat_json(
                system=DESCRIBE_SYSTEM,
                user_parts=parts,
                model=model,
                schema=SceneDescription.model_json_schema(),
                temperature=0.2,
                max_tokens=500,
            )
            record = SceneDescription.coerce(data)
        except PlannerOutputError:
            failed += 1
            continue
        await conn.execute(
            "INSERT OR REPLACE INTO scene_descriptions("
            "scene_id, description, objects, actions, camera_motion, people, emotion, "
            "scene_type, importance_score, highlight_score, model_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(row["id"]),
                record.description,
                json.dumps(record.objects),
                json.dumps(record.actions),
                record.camera_motion,
                record.people,
                record.emotion,
                record.scene_type,
                record.importance_score,
                record.highlight_score,
                model,
            ),
        )
        described += 1
        await conn.commit()
    return {"described": described, "failed": failed}
