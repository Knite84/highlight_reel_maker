import asyncio
from typing import Any

import numpy as np

from ..ai.embeddings import MODEL_ID, Embedder, blob_to_vector


async def search_scenes(
    conn: Any,
    query: str | None,
    *,
    kind: str | None = None,
    tag: str | None = None,
    limit: int = 40,
) -> list[dict]:
    sql = (
        "SELECT s.id, s.start_sec, s.end_sec, s.rep_frame_path, f.rel_path, f.kind, e.vector "
        "FROM embeddings e "
        "JOIN scenes s ON s.id = e.scene_id "
        "JOIN files f ON f.id = s.file_id "
        "WHERE e.model_id = ?"
    )
    params: list[Any] = [MODEL_ID]
    if kind in ("video", "photo"):
        sql += " AND f.kind = ?"
        params.append(kind)
    if tag:
        sql += (
            " AND EXISTS (SELECT 1 FROM scene_tags st WHERE st.scene_id = s.id AND st.tag = ?)"
        )
        params.append(tag)
    sql += " ORDER BY s.created_at DESC LIMIT 4000"
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    if not rows:
        return []

    vectors = np.stack([blob_to_vector(row["vector"]) for row in rows])
    if query:
        embedder = Embedder.get()
        query_vector = (await asyncio.to_thread(embedder.encode_texts, [query]))[0]
        scores = vectors @ query_vector
    else:
        scores = np.ones(len(rows))

    order = np.argsort(-scores)[:limit]
    scene_ids = [int(rows[int(i)]["id"]) for i in order]
    tags_by_scene: dict[int, list[dict]] = {}
    if scene_ids:
        placeholders = ",".join("?" * len(scene_ids))
        cur = await conn.execute(
            f"SELECT scene_id, tag, score FROM scene_tags WHERE scene_id IN ({placeholders})",
            scene_ids,
        )
        for tag_row in await cur.fetchall():
            tags_by_scene.setdefault(int(tag_row["scene_id"]), []).append(
                {"tag": tag_row["tag"], "score": round(tag_row["score"], 3)}
            )

    results = []
    for position, i in enumerate(order):
        row = rows[int(i)]
        results.append(
            {
                "scene_id": int(row["id"]),
                "rel_path": row["rel_path"],
                "kind": row["kind"],
                "start_sec": row["start_sec"],
                "end_sec": row["end_sec"],
                "thumb_rel": row["rep_frame_path"],
                "score": round(float(scores[int(i)]), 4),
                "tags": sorted(
                    tags_by_scene.get(int(row["id"]), []),
                    key=lambda item: -item["score"],
                ),
                "_rank": int(position),
            }
        )
    results.sort(key=lambda item: -item["score"])
    for result in results:
        result.pop("_rank")
    return results


async def list_tags(conn: Any, limit: int = 60) -> list[dict]:
    cur = await conn.execute(
        "SELECT tag, COUNT(*) AS count FROM scene_tags GROUP BY tag ORDER BY count DESC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    return [{"tag": row["tag"], "count": int(row["count"])} for row in rows]
