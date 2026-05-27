"""Self-improving few-shot: подсасываем последние approved драфты как примеры.

Зачем: с каждым одобренным драфтом LLM лучше понимает «что именно Аня
одобряет» — без перетренировки и file-tuning'а.

Используется в prompts.py для блока «ПРИМЕРЫ ОДОБРЕННЫХ ПОСТОВ».
Фильтр: тот же voice_profile + channel + (опционально) form / segment.

На v0 будет пусто (нет approved драфтов) — это нормально.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg


def pull_approved_examples(
    conn: "psycopg.Connection",
    *,
    voice_profile_slug: str,
    channel_slug: str,
    content_form_slug: str | None = None,
    segment_slug: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Последние N approved драфтов для конфига, отсортированные по дате DESC."""
    where = [
        "status = 'approved'",
        "voice_profile_slug = %(voice)s",
        "channel_slug = %(channel)s",
    ]
    params: dict[str, Any] = {
        "voice": voice_profile_slug,
        "channel": channel_slug,
        "limit": limit,
    }
    if content_form_slug:
        where.append("content_form_slug = %(form)s")
        params["form"] = content_form_slug
    if segment_slug:
        where.append("segment_slug = %(seg)s")
        params["seg"] = segment_slug

    sql = f"""
    SELECT id::text, topic_hint, content, reviewed_at
    FROM content_drafts
    WHERE {' AND '.join(where)}
    ORDER BY reviewed_at DESC NULLS LAST
    LIMIT %(limit)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def format_few_shot_block(examples: list[dict]) -> str:
    """Текст для секции в промте. Пусто = `(нет одобренных примеров)`."""
    if not examples:
        return "(на данный момент нет одобренных примеров — не моделируй пример, генерируй с нуля)"
    lines = ["Эти драфты автор уже одобрила — ИХ СТИЛЬ И ТОН эталонные:"]
    for i, ex in enumerate(examples, 1):
        hint = (ex.get("topic_hint") or "—").strip()
        body = (ex.get("content") or "").strip()
        lines.append(f"\n--- Пример {i} (тема: {hint}) ---\n{body}")
    return "\n".join(lines)
