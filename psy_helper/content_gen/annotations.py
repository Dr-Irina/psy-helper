"""CRUD для source_annotations — обратная связь на исходные документы.

Заметки накапливаются Анной через UI, потом используются при regen'е
следующей версии voice_doc / lexicon / forbidden_topics.

Применение в regen-скриптах:
    open_annot = list_annotations(conn, source_type='voice_doc', status='open')
    # подмешиваем в Map-Reduce промт как «правки от автора»
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg


VERDICTS = ("good", "bad", "fix", "neutral")
STATUSES = ("open", "addressed", "wontfix")

# Человеко-читаемые ярлыки и эмодзи
VERDICT_LABELS = {
    "good": "👍 хорошо",
    "bad": "👎 убрать",
    "fix": "✏ правка",
    "neutral": "💭 заметка",
}
STATUS_LABELS = {
    "open": "🟢 открыта",
    "addressed": "✅ применено",
    "wontfix": "⊘ не править",
}


def save_annotation(
    conn: "psycopg.Connection",
    *,
    therapist_id: str,
    source_type: str,
    source_id: str,
    verdict: str,
    comment: str | None = None,
    line_anchor: str | None = None,
    author: str = "UI",
) -> str:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    sql = """
    INSERT INTO source_annotations
        (therapist_id, source_type, source_id, line_anchor,
         verdict, comment, author)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    RETURNING id::text
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            therapist_id, source_type, source_id, line_anchor or None,
            verdict, comment or None, author,
        ))
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def list_annotations(
    conn: "psycopg.Connection",
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    status: str | None = None,
    verdict: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if source_type:
        where.append("source_type = %(stype)s"); params["stype"] = source_type
    if source_id:
        where.append("source_id = %(sid)s"); params["sid"] = source_id
    if status:
        where.append("status = %(status)s"); params["status"] = status
    if verdict:
        where.append("verdict = %(verdict)s"); params["verdict"] = verdict

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    sql = f"""
    SELECT id::text, source_type, source_id, line_anchor,
           verdict, comment, status, addressed_in_version,
           author, created_at, addressed_at
    FROM source_annotations
    {where_sql}
    ORDER BY status = 'open' DESC, created_at DESC
    LIMIT %(limit)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def update_annotation_status(
    conn: "psycopg.Connection",
    annotation_id: str,
    *,
    status: str,
    addressed_in_version: str | None = None,
) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    sql = """
    UPDATE source_annotations
    SET status = %(status)s,
        addressed_in_version = COALESCE(%(ver)s, addressed_in_version),
        addressed_at = CASE WHEN %(status)s IN ('addressed', 'wontfix')
                            THEN NOW() ELSE addressed_at END
    WHERE id = %(id)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"id": annotation_id, "status": status, "ver": addressed_in_version})
    conn.commit()


def delete_annotation(conn: "psycopg.Connection", annotation_id: str) -> None:
    """Удаление физически — для опечаток. Для штатного 'отзыва' — wontfix."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM source_annotations WHERE id = %s", (annotation_id,))
    conn.commit()


def count_open_for(
    conn: "psycopg.Connection",
    source_type: str,
    source_id: str,
) -> int:
    """Сколько открытых заметок на конкретный source. Для бейджей в UI."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM source_annotations "
            "WHERE source_type = %s AND source_id = %s AND status = 'open'",
            (source_type, source_id),
        )
        return cur.fetchone()[0]
