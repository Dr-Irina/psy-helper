"""CRUD для content_drafts.

Все операции через context manager соединения; saver сам не commit'ит до конца
функции (вызывающий может откатить).

Идентификация терапевта: по `name` в таблице therapists. На MVP-0 один — Анна.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from psy_helper.content_gen.config import ContentDraft, GenerationConfig

if TYPE_CHECKING:
    import psycopg


def get_therapist_id(conn: "psycopg.Connection", name: str = "Анна") -> str:
    """Резолвит therapist.id по name. Создаёт если нет (для idempotency)."""
    with conn.cursor() as cur:
        cur.execute("SELECT id::text FROM therapists WHERE name = %s", (name,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO therapists(name) VALUES (%s) RETURNING id::text",
            (name,),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def save_draft(
    conn: "psycopg.Connection",
    *,
    therapist_id: str,
    cfg: GenerationConfig,
    draft: ContentDraft,
) -> str:
    """INSERT + commit. Возвращает UUID нового draft'а."""
    sql = """
    INSERT INTO content_drafts (
        therapist_id,
        voice_profile_slug, channel_slug, content_form_slug,
        segment_slug, psycho_type_slug,
        hunt_stage, topics, topic_hint,
        content, provenance,
        prompt_version, config_snapshot,
        model, cost_usd, tokens_input, tokens_output,
        cache_creation_tokens, cache_read_tokens,
        pii_flags, status,
        generation_duration_ms
    )
    VALUES (
        %(therapist_id)s,
        %(voice)s, %(channel)s, %(form)s,
        %(seg)s, %(pt)s,
        %(stage)s, %(topics)s, %(topic_hint)s,
        %(content)s, %(prov)s,
        %(pv)s, %(snap)s,
        %(model)s, %(cost)s, %(t_in)s, %(t_out)s,
        %(t_cc)s, %(t_cr)s,
        %(pii)s, %(status)s,
        %(dur_ms)s
    )
    RETURNING id::text
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "therapist_id": therapist_id,
            "voice": cfg.voice_profile,
            "channel": cfg.channel,
            "form": cfg.content_form,
            "seg": cfg.segment,
            "pt": cfg.psycho_type,
            "stage": cfg.hunt_stage,
            "topics": cfg.topics or None,
            "topic_hint": cfg.topic_hint,
            "content": draft.content,
            "prov": json.dumps(draft.provenance, ensure_ascii=False),
            "pv": draft.prompt_version,
            "snap": json.dumps(draft.config_snapshot, ensure_ascii=False, default=str),
            "model": draft.model,
            "cost": draft.cost.cost_usd,
            "t_in": draft.cost.tokens_input,
            "t_out": draft.cost.tokens_output,
            "t_cc": draft.cost.cache_creation_tokens,
            "t_cr": draft.cost.cache_read_tokens,
            "pii": draft.pii_flags or None,
            "status": "draft",
            "dur_ms": draft.generation_duration_ms,
        })
        draft_id = cur.fetchone()[0]
    conn.commit()
    return draft_id


def load_draft(conn: "psycopg.Connection", draft_id: str) -> dict[str, Any]:
    """SELECT one. Возвращает dict с человекочитаемыми ключами."""
    sql = """
    SELECT id::text, therapist_id::text,
           voice_profile_slug, channel_slug, content_form_slug,
           segment_slug, psycho_type_slug,
           hunt_stage, topics, topic_hint,
           content, provenance,
           prompt_version, config_snapshot,
           model, cost_usd, tokens_input, tokens_output,
           cache_creation_tokens, cache_read_tokens,
           pii_flags, status, reviewed_by, review_notes, failure_reason,
           created_at, reviewed_at, published_at, generation_duration_ms
    FROM content_drafts WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (draft_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError(f"draft {draft_id} not found")
        cols = [d.name for d in cur.description]
    return dict(zip(cols, row))


def update_status(
    conn: "psycopg.Connection",
    draft_id: str,
    *,
    status: str,
    reviewed_by: str | None = None,
    review_notes: str | None = None,
) -> None:
    """Apply status transition + audit."""
    sql = """
    UPDATE content_drafts
    SET status = %(status)s,
        reviewed_by = COALESCE(%(by)s, reviewed_by),
        review_notes = COALESCE(%(notes)s, review_notes),
        reviewed_at = NOW(),
        published_at = CASE WHEN %(status)s = 'published' THEN NOW() ELSE published_at END
    WHERE id = %(id)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "id": draft_id,
            "status": status,
            "by": reviewed_by,
            "notes": review_notes,
        })
    conn.commit()


def list_drafts(
    conn: "psycopg.Connection",
    *,
    status: str | None = None,
    voice_profile: str | None = None,
    channel: str | None = None,
    segment: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Для UI «📋 Черновики» — фильтры + последние N."""
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if status:
        where.append("status = %(status)s"); params["status"] = status
    if voice_profile:
        where.append("voice_profile_slug = %(voice)s"); params["voice"] = voice_profile
    if channel:
        where.append("channel_slug = %(channel)s"); params["channel"] = channel
    if segment:
        where.append("segment_slug = %(segment)s"); params["segment"] = segment

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    sql = f"""
    SELECT id::text, voice_profile_slug, channel_slug, content_form_slug,
           segment_slug, hunt_stage, topic_hint,
           LEFT(content, 200) AS preview,
           cost_usd, status, created_at
    FROM content_drafts
    {where_sql}
    ORDER BY created_at DESC
    LIMIT %(limit)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
