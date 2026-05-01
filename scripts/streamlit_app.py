"""Минимальный справочный интерфейс по методу Анны (МVP-0).

Поле ввода → семантический поиск по concepts + clean_segments → результаты.
Фильтр по типу концепта. Просмотр активной voice-doc.

Запуск:
    docker compose up -d ui
    # → открыть http://localhost:8501
"""
from __future__ import annotations

import streamlit as st
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from psy_helper.db.connection import connect
from psy_helper.taxonomy import CONCEPT_TYPES

load_dotenv()

MODEL_NAME = "intfloat/multilingual-e5-large"


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


def search_concepts(conn, embedding, types: list[str] | None, limit: int = 12):
    where = ""
    params: list = [embedding]
    if types:
        where = "WHERE type = ANY(%s)"
        params.append(types)
    params.extend([embedding, limit])
    sql = f"""
        SELECT name, type, description, 1 - (embedding <=> %s) AS score,
               array_length(source_segments, 1) AS sources_count
        FROM concepts
        {where}
        ORDER BY embedding <=> %s
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def search_segments(conn, embedding, limit: int = 6):
    sql = """
        SELECT cs.title, cs.summary, cs.start_ts, cs.end_ts,
               rt.source_file,
               1 - (se.embedding <=> %s) AS score
        FROM clean_segments cs
        JOIN segment_embeddings se ON se.segment_id = cs.id
        JOIN raw_transcripts rt ON rt.id = cs.raw_id
        ORDER BY se.embedding <=> %s
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (embedding, embedding, limit))
        return cur.fetchall()


def get_active_voice_doc(conn, therapist_name: str = "Анна") -> tuple[int | None, str | None]:
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


def db_stats(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM raw_transcripts")
        lectures = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM clean_segments")
        segments = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM concepts")
        concepts = cur.fetchone()[0]
    return {"lectures": lectures, "segments": segments, "concepts": concepts}


# --- UI ---

st.set_page_config(page_title="psy-helper", layout="wide", page_icon="📚")

conn = get_conn()
stats = db_stats(conn)

st.title("📚 База знаний — метод Анны")
st.caption(
    f"{stats['lectures']} лекций · {stats['segments']} смысловых блоков · "
    f"{stats['concepts']} концептов. Спрашивай своими словами."
)

with st.sidebar:
    st.header("Фильтры")
    selected_types = st.multiselect(
        "Типы концептов",
        list(CONCEPT_TYPES.keys()),
        default=[],
        format_func=lambda t: f"{t} — {CONCEPT_TYPES[t][:50]}…",
        help="Пусто = все типы",
    )

    st.divider()
    st.header("Voice-document")
    version, vd_content = get_active_voice_doc(conn)
    if vd_content:
        st.caption(f"Активная версия: v{version}")
        with st.expander("Открыть"):
            st.markdown(vd_content)
    else:
        st.caption("Активной версии нет.")

query = st.text_input(
    "Что найти?",
    placeholder="например: как слушать партнёра / что почитать / когда обращаться к терапевту",
)

if query:
    with st.spinner("Поиск…"):
        v = get_model().encode(
            [f"query: {query}"], normalize_embeddings=True
        )[0]
        concepts = search_concepts(conn, v, selected_types or None)
        segments = search_segments(conn, v)

    col_c, col_s = st.columns([3, 2], gap="large")

    with col_c:
        st.subheader(f"🧩 Концепты ({len(concepts)})")
        if not concepts:
            st.info("Ничего не нашлось — попробуй другие слова.")
        for name, ctype, description, score, sources_count in concepts:
            type_label = ctype
            sources_part = f" · упоминается в {sources_count} блоках" if sources_count else ""
            with st.expander(
                f"**{name}** · _{type_label}_ · {score:.2f}{sources_part}"
            ):
                st.write(description or "")

    with col_s:
        st.subheader(f"📖 Блоки лекций ({len(segments)})")
        for title, summary, start_ts, end_ts, source_file, score in segments:
            lecture = source_file.split("/")[-2]
            ts = fmt_ts_range(start_ts, end_ts)
            with st.expander(f"**{title}** · _{ts}_ · {score:.2f}"):
                st.write(summary or "")
                st.caption(f"📁 {lecture}")
else:
    st.markdown(
        """
        ### Как пользоваться
        - Задавай вопросы как клиент или как сама Анна — формулировки своими словами
        - Слева в сайдбаре можно отфильтровать по типу концепта (термин/техника/предостережение/…)
        - Каждая карточка раскрывается по клику; внизу указана лекция-источник
        - Voice-document активной версии — в сайдбаре

        ### Примеры запросов
        - «как слушать партнёра»
        - «что почитать про коммуникацию»
        - «когда обращаться к терапевту»
        - «почему я обижаюсь»
        - «упражнения на каждый день»
        """
    )
