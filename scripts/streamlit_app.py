"""Справочный интерфейс по методу Анны (МVP-0).

Tabs:
  1. Поиск      — гибридный (BM25+vector) по concepts и clean_segments
  2. По типам   — все концепты одного типа списком
  3. По лекциям — выбрать лекцию, увидеть её блоки и привязанные концепты
  4. Похожие    — выбрать концепт, увидеть близкие по смыслу

Запуск:
    docker compose up -d ui
    # → http://localhost:8501
"""
from __future__ import annotations

import streamlit as st
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from psy_helper.db.connection import connect
from psy_helper.search import hybrid_search_concepts, hybrid_search_segments
from psy_helper.taxonomy import CONCEPT_TYPES

load_dotenv()

MODEL_NAME = "intfloat/multilingual-e5-large"

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


@st.cache_resource(show_spinner="Загрузка модели эмбеддингов…")
def get_model() -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME)


@st.cache_resource
def get_conn():
    conn = connect()
    register_vector(conn)
    return conn


def fmt_ts_range(start: float, end: float) -> str:
    sm, ss = divmod(int(start), 60)
    em, es = divmod(int(end), 60)
    return f"{sm}:{ss:02d}–{em}:{es:02d}"


def lecture_name(source_file: str) -> str:
    return source_file.split("/")[-2]


def do_search_concepts(conn, query_text: str, embedding, types, limit=12):
    with conn.cursor() as cur:
        return hybrid_search_concepts(cur, query_text, embedding, types=types, limit=limit)


def do_search_segments(conn, query_text: str, embedding, limit=6):
    with conn.cursor() as cur:
        return hybrid_search_segments(cur, query_text, embedding, limit=limit)


def get_active_voice_doc(conn, therapist_name="Анна"):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT vd.version, vd.content
            FROM voice_document vd
            JOIN therapists t ON t.id = vd.therapist_id
            WHERE t.name = %s AND vd.is_active = TRUE
            ORDER BY vd.version DESC LIMIT 1
            """,
            (therapist_name,),
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)


def db_stats(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM raw_transcripts")
        lectures = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM clean_segments")
        segments = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM concepts")
        concepts = cur.fetchone()[0]
    return {"lectures": lectures, "segments": segments, "concepts": concepts}


def type_counts(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT type, COUNT(*) FROM concepts GROUP BY type ORDER BY 2 DESC"
        )
        return dict(cur.fetchall())


def all_lectures(conn):
    """Список лекций для выпадайки. Возвращает [(raw_id, name, segment_count)]."""
    with conn.cursor() as cur:
        cur.execute(
            """
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
            """
        )
        return [(r[0], lecture_name(r[1]), r[2], r[3]) for r in cur.fetchall()]


def lecture_segments(conn, raw_id: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, title, summary, start_ts, end_ts
            FROM clean_segments
            WHERE raw_id = %s
            ORDER BY start_ts
            """,
            (raw_id,),
        )
        return cur.fetchall()


def lecture_concepts(conn, raw_id: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT c.id::text, c.name, c.type, c.description
            FROM concepts c
            WHERE c.source_segments && ARRAY(
                SELECT id FROM clean_segments WHERE raw_id = %s
            )
            ORDER BY c.type, c.name
            """,
            (raw_id,),
        )
        return cur.fetchall()


def concepts_of_type(conn, type_name: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, description,
                   array_length(source_segments, 1) AS sources_count
            FROM concepts
            WHERE type = %s
            ORDER BY sources_count DESC NULLS LAST, name
            """,
            (type_name,),
        )
        return cur.fetchall()


def all_concept_names(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, name, type FROM concepts ORDER BY name"
        )
        return cur.fetchall()


def similar_concepts(conn, concept_id: str, limit: int = 12):
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH target AS (
              SELECT embedding FROM concepts WHERE id = %s
            )
            SELECT c.id::text, c.name, c.type, c.description,
                   1 - (c.embedding <=> t.embedding) AS sim,
                   array_length(c.source_segments, 1) AS sources_count
            FROM concepts c, target t
            WHERE c.id != %s AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> t.embedding
            LIMIT %s
            """,
            (concept_id, concept_id, limit),
        )
        return cur.fetchall()


def co_occurring_concepts(conn, concept_id: str, limit: int = 10):
    """Концепты, упоминающиеся в тех же блоках, что и заданный."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id::text, c.name, c.type,
                   COUNT(*) AS shared_segments
            FROM concepts c
            WHERE c.id != %s
              AND c.source_segments && (
                SELECT source_segments FROM concepts WHERE id = %s
              )
            GROUP BY c.id, c.name, c.type
            ORDER BY shared_segments DESC, c.name
            LIMIT %s
            """,
            (concept_id, concept_id, limit),
        )
        return cur.fetchall()


# --- UI ---

st.set_page_config(page_title="psy-helper", layout="wide", page_icon="📚")

conn = get_conn()
stats = db_stats(conn)

st.title("📚 База знаний — метод Анны")
st.caption(
    f"{stats['lectures']} лекций · {stats['segments']} смысловых блоков · "
    f"{stats['concepts']} концептов"
)

with st.sidebar:
    st.header("Voice-document")
    version, vd_content = get_active_voice_doc(conn)
    if vd_content:
        st.caption(f"Активная версия: v{version}")
        with st.expander("Открыть"):
            st.markdown(vd_content)
    else:
        st.caption("Активной версии нет.")

tab_search, tab_types, tab_lectures, tab_related = st.tabs(
    ["🔍 Поиск", "🧩 По типам", "📖 По лекциям", "🔗 Похожие"]
)

# --- Tab: Поиск ---
with tab_search:
    selected_types = st.multiselect(
        "Фильтр по типам (опционально)",
        list(CONCEPT_TYPES.keys()),
        default=[],
        format_func=lambda t: f"{t} — {TYPE_LABELS.get(t, t)}",
        key="search_types",
    )
    query = st.text_input(
        "Что найти?",
        placeholder="например: как слушать партнёра / что почитать / когда обращаться к терапевту",
    )

    if query:
        with st.spinner("Поиск…"):
            v = get_model().encode(
                [f"query: {query}"], normalize_embeddings=True
            )[0]
            concepts = do_search_concepts(conn, query, v, selected_types or None)
            segments = do_search_segments(conn, query, v)

        col_c, col_s = st.columns([3, 2], gap="large")
        with col_c:
            st.subheader(f"🧩 Концепты ({len(concepts)})")
            if not concepts:
                st.info("Ничего не нашлось — попробуй другие слова.")
            for c in concepts:
                sources_part = (
                    f" · в {c.sources_count} блоках" if c.sources_count else ""
                )
                with st.expander(
                    f"**{c.name}** · _{c.type}_ · {c.score:.3f}{sources_part}"
                ):
                    st.write(c.description or "")

        with col_s:
            st.subheader(f"📖 Блоки лекций ({len(segments)})")
            for s in segments:
                ts = fmt_ts_range(s.start_ts, s.end_ts)
                with st.expander(f"**{s.title}** · _{ts}_ · {s.score:.3f}"):
                    st.write(s.summary or "")
                    st.caption(f"📁 {lecture_name(s.source_file)}")
    else:
        st.info(
            "Задай вопрос как клиент или как Анна — своими словами. "
            "Поиск гибридный: BM25 (по словам) + векторный (по смыслу). "
            "Фильтр по типу — опционально."
        )

# --- Tab: По типам ---
with tab_types:
    counts = type_counts(conn)
    options = list(CONCEPT_TYPES.keys())
    chosen_type = st.selectbox(
        "Тип концептов",
        options,
        format_func=lambda t: f"{TYPE_LABELS.get(t, t)} ({counts.get(t, 0)})",
        key="browse_type",
    )
    items = concepts_of_type(conn, chosen_type)
    st.caption(
        f"{len(items)} концептов типа _{TYPE_LABELS.get(chosen_type, chosen_type)}_. "
        f"Сортировка: по количеству источников, затем по алфавиту."
    )
    for name, description, sources_count in items:
        sources_part = f" · в {sources_count} блоках" if sources_count else ""
        with st.expander(f"**{name}**{sources_part}"):
            st.write(description or "")

# --- Tab: По лекциям ---
with tab_lectures:
    lectures = all_lectures(conn)
    if lectures:
        chosen = st.selectbox(
            "Лекция",
            lectures,
            format_func=lambda L: f"{L[1]} ({L[2]} блоков, {L[3]} концептов)",
            key="browse_lecture",
        )
        raw_id, lec_name, n_segs, n_cons = chosen

        col1, col2 = st.columns([3, 2], gap="large")

        with col1:
            st.subheader(f"📖 Блоки ({n_segs})")
            for seg_id, title, summary, start_ts, end_ts in lecture_segments(conn, raw_id):
                ts = fmt_ts_range(start_ts, end_ts)
                with st.expander(f"**{title}** · _{ts}_"):
                    st.write(summary or "")

        with col2:
            st.subheader(f"🧩 Концепты ({n_cons})")
            grouped = {}
            for cid, name, ctype, description in lecture_concepts(conn, raw_id):
                grouped.setdefault(ctype, []).append((name, description))
            for ctype in CONCEPT_TYPES.keys():
                if ctype not in grouped:
                    continue
                items = grouped[ctype]
                with st.expander(
                    f"**{TYPE_LABELS.get(ctype, ctype)}** ({len(items)})"
                ):
                    for name, description in items:
                        st.markdown(f"- **{name}** — {description or ''}")

# --- Tab: Похожие ---
with tab_related:
    all_names = all_concept_names(conn)
    if all_names:
        chosen = st.selectbox(
            "Концепт",
            all_names,
            format_func=lambda c: f"{c[1]} ({c[2]})",
            key="related_concept",
        )
        cid, name, ctype = chosen

        col1, col2 = st.columns(2, gap="large")
        with col1:
            st.subheader("🔗 По смыслу")
            st.caption("Близкие по эмбеддингу — обычно связанная тематика")
            for sid, sname, stype, sdesc, sim, sources_count in similar_concepts(conn, cid):
                sp = f" · в {sources_count} блоках" if sources_count else ""
                with st.expander(f"**{sname}** · _{stype}_ · sim {sim:.3f}{sp}"):
                    st.write(sdesc or "")

        with col2:
            st.subheader("📍 Из тех же блоков")
            st.caption(
                "Концепты, которые встречаются в одних и тех же блоках лекций — "
                "Анна обсуждает их вместе"
            )
            for sid, sname, stype, shared in co_occurring_concepts(conn, cid):
                with st.expander(f"**{sname}** · _{stype}_ · {shared} общих блоков"):
                    pass
