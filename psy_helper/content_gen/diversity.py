"""Anti-repeat: подсказки генератору о том, что уже писали для (channel, segment).

Цель: избегать повторов хуков, метафор и тем в соседних драфтах.
Метод: pull последних N approved/draft из content_drafts по (channel, segment),
извлекаем первые ~100 символов (hook) + topic_hint и передаём LLM как
«вот что мы уже писали — не повторяйся».

НЕ блокирующий фильтр — только hint в промте. Жёсткое сходство (cosine)
оставлено на Phase 3+ (Reranking).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg


def get_recent_drafts_hints(
    conn: "psycopg.Connection",
    *,
    channel_slug: str,
    segment_slug: str | None,
    limit: int = 5,
) -> list[dict]:
    """Вернуть последние N драфтов для (channel, segment) — hook + topic_hint.

    Используется в prompts.py для секции «Чего избегать (последние твои посты)».
    """
    sql = """
    SELECT id::text, topic_hint, LEFT(content, 200) AS hook, created_at
    FROM content_drafts
    WHERE channel_slug = %(channel)s
      AND status IN ('draft', 'approved')
      AND (%(seg)s::text IS NULL OR segment_slug = %(seg)s)
    ORDER BY created_at DESC
    LIMIT %(limit)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "channel": channel_slug,
            "seg": segment_slug,
            "limit": limit,
        })
        rows = cur.fetchall()

    return [
        {"id": r[0], "topic_hint": r[1], "hook": r[2], "created_at": r[3]}
        for r in rows
    ]


def format_diversity_hint(recent: list[dict]) -> str:
    """Текст для секции в системном промте."""
    if not recent:
        return "(это первый драфт для этого канала и сегмента)"
    lines = ["Недавние посты для этого канала и сегмента — НЕ повторяй их хуки и темы:"]
    for d in recent:
        hint = (d.get("topic_hint") or "").strip()
        hook = (d.get("hook") or "").strip().replace("\n", " ")[:120]
        prefix = f"— [{hint}] " if hint else "— "
        lines.append(f"{prefix}{hook}…")
    return "\n".join(lines)
