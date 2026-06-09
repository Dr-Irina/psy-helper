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
    """Cookie-based auth через streamlit-authenticator.

    Без env STREAMLIT_PASSWORD — bypass (dev mode).
    С STREAMLIT_PASSWORD требуется ещё STREAMLIT_COOKIE_KEY (для подписи cookie).
    Username фиксированный: `anna`. Cookie живёт 30 дней — F5 и закрытие
    браузера на это время не сбросят сессию.
    """
    expected = os.getenv("STREAMLIT_PASSWORD", "").strip()
    if not expected:
        return  # dev bypass

    cookie_key = os.getenv("STREAMLIT_COOKIE_KEY", "").strip()
    if not cookie_key:
        st.error(
            "Сервер настроен некорректно: STREAMLIT_COOKIE_KEY не задан. "
            "Задай его в .env (`openssl rand -hex 32`) и перезапусти."
        )
        st.stop()

    import streamlit_authenticator as stauth

    hashed = _get_hashed_password(expected)
    credentials = {
        "usernames": {
            "anna": {
                "name": "Анна",
                "password": hashed,
                "email": "anna@psy-helper.local",
                "logged_in": False,
                "first_name": "Анна",
                "last_name": "",
            }
        }
    }
    authenticator = stauth.Authenticate(
        credentials,
        cookie_name="psy_helper_auth",
        cookie_key=cookie_key,
        cookie_expiry_days=30,
    )

    auth_status = st.session_state.get("authentication_status")

    # Форму login рисуем ТОЛЬКО когда ещё не залогинены (иначе она остаётся
    # видна сверху приложения)
    if not auth_status:
        try:
            authenticator.login(
                location="main",
                fields={"Form name": "🔒 Вход", "Username": "Имя",
                        "Password": "Пароль", "Login": "Войти"},
            )
        except Exception as e:
            st.error(f"Ошибка авторизации: {e}")
            st.stop()
        # Перечитываем статус после попытки login
        auth_status = st.session_state.get("authentication_status")

    if auth_status is True:
        with st.sidebar:
            try:
                authenticator.logout(button_name="Выйти", location="sidebar")
            except Exception:
                pass
        return  # пускаем в приложение
    elif auth_status is False:
        st.error("Неверный пароль")
        st.stop()
    else:
        st.info("Введи имя (`anna`) и пароль, чтобы войти")
        st.stop()


@st.cache_resource
def _get_hashed_password(password: str) -> str:
    """Хеш считается один раз на старте приложения и кешируется."""
    import streamlit_authenticator as stauth
    return stauth.Hasher.hash(password)


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
            SELECT id::text, title, summary, text, start_ts, end_ts
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


def browse_concepts_by_types(conn, types: list[str]) -> list:
    """Каталог: все концепты выбранных типов, сортировка по числу источников.

    Возвращает объекты ConceptHit-like (id/name/type/description/sources_count/score)
    чтобы _render_concept мог их съесть без изменений.
    """
    from psy_helper.search import ConceptHit

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id::text, name, type, description,
                   array_length(source_segments, 1) AS sources_count
            FROM concepts
            WHERE type = ANY(%s)
            ORDER BY sources_count DESC NULLS LAST, name
        """, (types,))
        rows = cur.fetchall()

    return [
        ConceptHit(
            id=r[0], name=r[1], type=r[2], description=r[3],
            sources_count=r[4], score=0.0,
            bm25_rank=None, vec_rank=None,
        )
        for r in rows
    ]


def all_concept_names(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, name, type FROM concepts ORDER BY name")
        return cur.fetchall()


def get_concept(conn, concept_id: str):
    """Полные данные одного концепта по id."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id::text, name, type, COALESCE(description, ''),
                   array_length(source_segments, 1) AS sources_count
            FROM concepts WHERE id = %s
        """, (concept_id,))
        return cur.fetchone()


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


def concept_source_segments(conn, concept_id: str) -> list:
    """Реальные блоки-источники для одного концепта.

    Возвращает: [(segment_id, raw_id, title, summary, text, start_ts, end_ts, source_file)]
    Используется в Поиске чтобы Анна могла открыть «откуда это знание».
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT cs.id::text, rt.id::text, cs.title, cs.summary, cs.text,
                   cs.start_ts, cs.end_ts, rt.source_file
            FROM clean_segments cs
            JOIN raw_transcripts rt ON rt.id = cs.raw_id
            WHERE cs.id = ANY(
                SELECT unnest(source_segments) FROM concepts WHERE id = %s
            )
            ORDER BY rt.source_file, cs.start_ts
        """, (concept_id,))
        return cur.fetchall()


def concept_voice(conn, concept_id: str) -> tuple[int | None, list[str]]:
    """Голос Ани: значимость (salience 1-3) + дословные цитаты концепта (тексты).

    Это то, что отличает v2-корпус — Анин голос, а не пересказ.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT salience, quotes FROM concepts WHERE id = %s", (concept_id,))
        row = cur.fetchone()
    if not row:
        return None, []
    salience, quotes = row
    texts: list[str] = []
    for q in (quotes or []):
        t = (q.get("text") if isinstance(q, dict) else str(q)) if q else ""
        if t and t.strip():
            texts.append(t.strip())
    return salience, texts


def co_occurring_concepts(conn, concept_id, limit=10):
    """Концепты из общих блоков. Возвращает id, name, type, description, shared_count."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.id::text, c.name, c.type, COALESCE(c.description, ''),
                   COUNT(*) AS shared_segments
            FROM concepts c
            WHERE c.id != %s
              AND c.source_segments && (
                SELECT source_segments FROM concepts WHERE id = %s
              )
            GROUP BY c.id, c.name, c.type, c.description
            ORDER BY shared_segments DESC, c.name LIMIT %s
        """, (concept_id, concept_id, limit))
        return cur.fetchall()


def shared_segments_between(conn, concept_id_a: str, concept_id_b: str) -> list:
    """Какие конкретно блоки общие у двух концептов (с таймкодом и текстом).

    Возвращает: [(segment_id, raw_id, title, summary, text, start_ts, end_ts, source_file)]
    Сигнатура совпадает с concept_source_segments — можно рендерить тем же helper'ом.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT cs.id::text, rt.id::text, cs.title, cs.summary, cs.text,
                   cs.start_ts, cs.end_ts, rt.source_file
            FROM clean_segments cs
            JOIN raw_transcripts rt ON rt.id = cs.raw_id
            WHERE cs.id = ANY(
                SELECT unnest(source_segments) FROM concepts WHERE id = %s
            )
            AND cs.id = ANY(
                SELECT unnest(source_segments) FROM concepts WHERE id = %s
            )
            ORDER BY rt.source_file, cs.start_ts
        """, (concept_id_a, concept_id_b))
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
