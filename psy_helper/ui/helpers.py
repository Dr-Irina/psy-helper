"""Shared helpers для streamlit-страниц: cached resources, auth, rate limit,
annotation widget, форматирование, SQL-запросы для базы знаний.
"""
from __future__ import annotations

import os
import time

import streamlit as st
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from psy_helper.content_gen.annotations import (
    STATUS_LABELS,
    VERDICT_LABELS,
    delete_annotation,
    list_annotations,
    save_annotation,
    update_annotation_status,
)
from psy_helper.content_gen.storage import get_therapist_id
from psy_helper.db.connection import connect
from psy_helper.search import (
    hybrid_search_concepts,
    hybrid_search_lexicon,
    hybrid_search_segments,
)

MODEL_NAME = "intfloat/multilingual-e5-large"

RATE_LIMIT_MAX = 10            # генераций
RATE_LIMIT_WINDOW = 300         # сек (5 минут)

TYPE_LABELS = {
    "term": "Термины",
    "technique": "Техники",
    "claim": "Утверждения",
    "warning": "Предостережения",
    "recommendation": "Рекомендации",
    "exercise": "Упражнения",
    "question": "Вопросы",
    "metaphor": "Метафоры",
    "example": "Примеры",
}


# ─── Auth + rate limit ────────────────────────────────────────────────────────

def gate_password() -> None:
    """Простой password gate. Без env STREAMLIT_PASSWORD — bypass (dev mode)."""
    expected = os.getenv("STREAMLIT_PASSWORD", "").strip()
    if not expected or st.session_state.get("auth_ok"):
        return
    st.title("🔒 psy-helper")
    pw = st.text_input("Пароль:", type="password")
    if pw == expected:
        st.session_state["auth_ok"] = True
        st.rerun()
    elif pw:
        st.error("Неверный пароль.")
    st.stop()


def check_rate_limit() -> tuple[bool, int]:
    now = time.time()
    ts = st.session_state.setdefault("gen_timestamps", [])
    ts[:] = [t for t in ts if now - t < RATE_LIMIT_WINDOW]
    if len(ts) >= RATE_LIMIT_MAX:
        wait = int(RATE_LIMIT_WINDOW - (now - ts[0])) + 1
        return False, wait
    return True, 0


def record_generation() -> None:
    st.session_state.setdefault("gen_timestamps", []).append(time.time())


# ─── Cached resources ────────────────────────────────────────────────────────

@st.cache_resource(show_spinner="Загрузка модели эмбеддингов…")
def get_model() -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME)


@st.cache_resource
def get_conn():
    conn = connect()
    register_vector(conn)
    return conn


# ─── Formatters ──────────────────────────────────────────────────────────────

def fmt_ts_range(start: float, end: float) -> str:
    sm, ss = divmod(int(start), 60)
    em, es = divmod(int(end), 60)
    return f"{sm}:{ss:02d}–{em}:{es:02d}"


def lecture_name(source_file: str) -> str:
    return source_file.split("/")[-2]


# ─── DB helpers для База знаний ──────────────────────────────────────────────

def do_search_concepts(conn, query_text, embedding, types, limit=12):
    with conn.cursor() as cur:
        return hybrid_search_concepts(cur, query_text, embedding, types=types, limit=limit)


def do_search_segments(conn, query_text, embedding, limit=6):
    with conn.cursor() as cur:
        return hybrid_search_segments(cur, query_text, embedding, limit=limit)


def do_search_lexicon(conn, query_text, embedding, kinds=None, limit=10):
    with conn.cursor() as cur:
        return hybrid_search_lexicon(cur, query_text, embedding, kinds=kinds, limit=limit)


def db_stats(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM raw_transcripts")
        lectures = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM clean_segments")
        segments = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM concepts")
        concepts = cur.fetchone()[0]
    return {"lectures": lectures, "segments": segments, "concepts": concepts}


def type_counts(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT type, COUNT(*) FROM concepts GROUP BY type ORDER BY 2 DESC")
        return dict(cur.fetchall())


def all_lectures(conn) -> list:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT rt.id::text, rt.source_file,
                   COUNT(DISTINCT cs.id) AS segs,
                   COUNT(DISTINCT c.id)  AS cons
            FROM raw_transcripts rt
            LEFT JOIN clean_segments cs ON cs.raw_id = rt.id
            LEFT JOIN concepts c ON c.source_segments && ARRAY(
                SELECT id FROM clean_segments WHERE raw_id = rt.id
            )
            GROUP BY rt.id, rt.source_file
            ORDER BY rt.source_file
        """)
        return [(r[0], lecture_name(r[1]), r[2], r[3]) for r in cur.fetchall()]


def lecture_segments(conn, raw_id):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id::text, title, summary, start_ts, end_ts
            FROM clean_segments WHERE raw_id = %s ORDER BY start_ts
        """, (raw_id,))
        return cur.fetchall()


def lecture_concepts(conn, raw_id):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT c.id::text, c.name, c.type, c.description
            FROM concepts c
            WHERE c.source_segments && ARRAY(
                SELECT id FROM clean_segments WHERE raw_id = %s
            )
            ORDER BY c.type, c.name
        """, (raw_id,))
        return cur.fetchall()


def concepts_of_type(conn, type_name):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT name, description,
                   array_length(source_segments, 1) AS sources_count
            FROM concepts WHERE type = %s
            ORDER BY sources_count DESC NULLS LAST, name
        """, (type_name,))
        return cur.fetchall()


def all_concept_names(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, name, type FROM concepts ORDER BY name")
        return cur.fetchall()


def similar_concepts(conn, concept_id, limit=12):
    with conn.cursor() as cur:
        cur.execute("""
            WITH target AS (SELECT embedding FROM concepts WHERE id = %s)
            SELECT c.id::text, c.name, c.type, c.description,
                   1 - (c.embedding <=> t.embedding) AS sim,
                   array_length(c.source_segments, 1) AS sources_count
            FROM concepts c, target t
            WHERE c.id != %s AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> t.embedding LIMIT %s
        """, (concept_id, concept_id, limit))
        return cur.fetchall()


def co_occurring_concepts(conn, concept_id, limit=10):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.id::text, c.name, c.type, COUNT(*) AS shared_segments
            FROM concepts c
            WHERE c.id != %s
              AND c.source_segments && (
                SELECT source_segments FROM concepts WHERE id = %s
              )
            GROUP BY c.id, c.name, c.type
            ORDER BY shared_segments DESC, c.name LIMIT %s
        """, (concept_id, concept_id, limit))
        return cur.fetchall()


# ─── Annotation widget ───────────────────────────────────────────────────────

def annotation_widget(conn, source_type: str, source_id: str, *,
                      key_suffix: str = "", label: str = "💬 Оставить заметку") -> None:
    """Мини-форма + список существующих заметок для (source_type, source_id)."""
    therapist_id = get_therapist_id(conn)
    existing = list_annotations(conn, source_type=source_type, source_id=source_id, limit=20)
    open_count = sum(1 for a in existing if a["status"] == "open")
    badge = f" · {open_count} открытых" if open_count else ""

    suffix = key_suffix or f"{source_type}:{source_id}"
    with st.expander(f"{label}{badge}", expanded=False):
        if existing:
            st.caption(f"Существующие заметки ({len(existing)}):")
            for a in existing:
                vlabel = VERDICT_LABELS.get(a["verdict"], a["verdict"])
                slabel = STATUS_LABELS.get(a["status"], a["status"])
                anchor = f" · «{a['line_anchor'][:40]}…»" if a["line_anchor"] else ""
                st.markdown(
                    f"- {vlabel} · {slabel} · "
                    f"{a['created_at'].strftime('%m-%d %H:%M')}{anchor}"
                )
                if a["comment"]:
                    st.markdown(f"  > {a['comment']}")
                if a["status"] == "open":
                    cols = st.columns(3)
                    if cols[0].button("✅ применено", key=f"addr_{suffix}_{a['id']}"):
                        update_annotation_status(conn, a["id"], status="addressed")
                        st.rerun()
                    if cols[1].button("⊘ не править", key=f"wont_{suffix}_{a['id']}"):
                        update_annotation_status(conn, a["id"], status="wontfix")
                        st.rerun()
                    if cols[2].button("🗑 удалить", key=f"del_{suffix}_{a['id']}"):
                        delete_annotation(conn, a["id"])
                        st.rerun()
            st.divider()

        verdict = st.radio(
            "Тип заметки:",
            ["good", "fix", "bad", "neutral"],
            format_func=lambda v: VERDICT_LABELS[v],
            horizontal=True, key=f"v_{suffix}",
        )
        anchor = st.text_input(
            "Якорь (опционально — кусочек строки):",
            key=f"a_{suffix}", placeholder="например: «истинная природа женщины»",
        )
        comment = st.text_area(
            "Комментарий:", key=f"c_{suffix}", height=80,
            placeholder="что не так / что хорошо / как лучше",
        )
        if st.button("💾 Сохранить заметку", key=f"save_{suffix}"):
            if not comment.strip() and not anchor.strip():
                st.warning("Хотя бы комментарий или якорь нужно заполнить.")
            else:
                save_annotation(
                    conn,
                    therapist_id=therapist_id,
                    source_type=source_type,
                    source_id=source_id,
                    verdict=verdict,
                    comment=comment.strip() or None,
                    line_anchor=anchor.strip() or None,
                )
                st.toast("Заметка сохранена")
                st.rerun()
