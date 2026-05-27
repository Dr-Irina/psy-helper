"""Справочный интерфейс по методу Анны (МVP-0) + content engine v0.

Tabs:
  1. Поиск       — гибридный (BM25+vector) по concepts и clean_segments
  2. По типам    — все концепты одного типа списком
  3. По лекциям  — выбрать лекцию, увидеть её блоки и привязанные концепты
  4. Похожие     — выбрать концепт, увидеть близкие по смыслу
  5. 🎨 Генератор — собрать черновик контента из 5 layers (Step 8)
  6. 📋 Черновики — фильтры по статусу + одобрить/отклонить/опубликовать

Запуск:
    docker compose up -d ui
    # → http://localhost:8501

Безопасность:
    Защищён простым password gate (env STREAMLIT_PASSWORD).
    Без пароля приложение не рендерится. Это не enterprise-auth,
    но защита от случайного захода на localhost/ngrok.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import streamlit as st
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from psy_helper.content_gen.config import GenerationConfig
from psy_helper.content_gen.generator import generate_streaming
from psy_helper.content_gen.loaders import (
    list_channels,
    list_content_forms,
    list_psycho_types,
    list_segments,
    list_voice_profiles,
    load_channel,
    load_content_form,
)
from psy_helper.content_gen.storage import (
    list_drafts,
    load_draft,
    update_status,
)
from psy_helper.db.connection import connect
from psy_helper.search import hybrid_search_concepts, hybrid_search_segments
from psy_helper.taxonomy import CONCEPT_TYPES

load_dotenv()

# ─── Auth + rate limit ────────────────────────────────────────────────────────

RATE_LIMIT_MAX = 10           # генераций
RATE_LIMIT_WINDOW = 300        # сек (5 минут)


def _gate_password() -> None:
    """Простой password gate. Без пароля приложение не рендерится."""
    expected = os.getenv("STREAMLIT_PASSWORD", "").strip()
    if not expected:
        # Если пароль не задан в env — это dev/локально, не блокируем.
        return
    if st.session_state.get("auth_ok"):
        return
    st.set_page_config(page_title="psy-helper · вход", layout="centered", page_icon="🔒")
    st.title("🔒 psy-helper")
    pw = st.text_input("Пароль:", type="password")
    if pw == expected:
        st.session_state["auth_ok"] = True
        st.rerun()
    elif pw:
        st.error("Неверный пароль.")
    st.stop()


def _check_rate_limit() -> tuple[bool, int]:
    """Returns (allowed, seconds_until_next_slot)."""
    now = time.time()
    ts = st.session_state.setdefault("gen_timestamps", [])
    # Сбрасываем старые отметки
    ts[:] = [t for t in ts if now - t < RATE_LIMIT_WINDOW]
    if len(ts) >= RATE_LIMIT_MAX:
        wait = int(RATE_LIMIT_WINDOW - (now - ts[0])) + 1
        return False, wait
    return True, 0


def _record_generation() -> None:
    st.session_state.setdefault("gen_timestamps", []).append(time.time())


_gate_password()

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

tab_search, tab_types, tab_lectures, tab_related, tab_gen, tab_drafts = st.tabs(
    ["🔍 Поиск", "🧩 По типам", "📖 По лекциям", "🔗 Похожие",
     "🎨 Генератор", "📋 Черновики"]
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

# --- Tab: 🎨 Генератор ---
with tab_gen:
    st.subheader("Сборка черновика контента")
    st.caption(
        "5 слоёв: голос × сегмент × психотип × канал × нарративная форма. "
        f"Лимит: {RATE_LIMIT_MAX} генераций / {RATE_LIMIT_WINDOW//60} минут на сессию."
    )

    col_a, col_b = st.columns(2, gap="medium")
    with col_a:
        voice_slug = st.selectbox("Голос (voice profile)", list_voice_profiles(),
                                  index=list_voice_profiles().index("anna_product")
                                  if "anna_product" in list_voice_profiles() else 0,
                                  key="gen_voice")
        channel_slug = st.selectbox("Канал", list_channels(),
                                    index=list_channels().index("tg_post")
                                    if "tg_post" in list_channels() else 0,
                                    key="gen_channel")
        form_slug = st.selectbox("Нарративная форма", list_content_forms(),
                                 index=list_content_forms().index("storytelling")
                                 if "storytelling" in list_content_forms() else 0,
                                 key="gen_form")
    with col_b:
        segment_slug = st.selectbox("Сегмент (опционально)", ["—"] + list_segments(),
                                    index=(["—"] + list_segments()).index("tired_wife")
                                    if "tired_wife" in list_segments() else 0,
                                    key="gen_segment")
        pt_slug = st.selectbox("Психотип (опционально)", ["—"] + list_psycho_types(),
                               index=(["—"] + list_psycho_types()).index("patient")
                               if "patient" in list_psycho_types() else 0,
                               key="gen_pt")
        hunt_stage = st.select_slider("Ступень Ханта", options=[None, 1, 2, 3, 4, 5],
                                      value=2, format_func=lambda v: "—" if v is None else str(v),
                                      key="gen_stage")

    topics_choice = st.multiselect(
        "Топики (фильтр корпуса)",
        ["marriage", "partnership", "children", "teens", "confidence",
         "personal_effectiveness", "finance", "communication", "general"],
        default=["marriage"], key="gen_topics",
    )
    topic_hint = st.text_input(
        "Конкретная тема (hint, опционально)",
        placeholder="например: границы в супружестве",
        key="gen_hint",
    )

    model_override = st.radio(
        "Модель",
        options=[None, "claude-sonnet-4-6", "claude-haiku-4-5"],
        format_func=lambda m: "из канала" if m is None else m.split("-")[1].capitalize(),
        horizontal=True, key="gen_model",
    )

    # Cost panel
    cum = st.session_state.get("cumulative_cost", 0.0)
    cum_n = st.session_state.get("cumulative_count", 0)
    st.caption(f"💰 За сессию: ${cum:.4f} · {cum_n} генераций")

    if st.button("🚀 Сгенерировать", type="primary", key="gen_run"):
        allowed, wait = _check_rate_limit()
        if not allowed:
            st.error(f"Превышен лимит ({RATE_LIMIT_MAX}/{RATE_LIMIT_WINDOW//60}мин). "
                     f"Подожди ~{wait} сек.")
        else:
            cfg = GenerationConfig(
                voice_profile=voice_slug,
                channel=channel_slug,
                content_form=form_slug,
                segment=None if segment_slug == "—" else segment_slug,
                psycho_type=None if pt_slug == "—" else pt_slug,
                hunt_stage=hunt_stage,
                topics=topics_choice,
                topic_hint=topic_hint or None,
                model_override=model_override,
            )

            ch = load_channel(channel_slug)
            cf = load_content_form(form_slug)
            st.caption(f"Канал: {ch.length.min_chars or '?'}–{ch.length.max_chars or '?'} chars · "
                       f"lexicon_min={cf.lexicon_min} · модель {model_override or ch.preferred_model}")

            placeholder = st.empty()
            placeholder.info("Загружаю модель эмбеддингов и подбираю материал из корпуса…")

            content_buf: list[str] = []
            draft_holder: dict = {}

            def _stream_capture():
                gen = generate_streaming(cfg, conn)
                while True:
                    try:
                        chunk = next(gen)
                        content_buf.append(chunk)
                        yield chunk
                    except StopIteration as e:
                        draft_holder["draft"] = e.value
                        return

            with placeholder.container():
                st.write_stream(_stream_capture())

            _record_generation()
            draft = draft_holder.get("draft")
            if draft:
                st.session_state["cumulative_cost"] = cum + draft.cost.cost_usd
                st.session_state["cumulative_count"] = cum_n + 1
                st.session_state["last_draft_id"] = draft.id

                # Quality flags
                quality_flags = [f for f in draft.pii_flags if not f.startswith(("name:", "phone:", "email:"))]
                pii_only = [f for f in draft.pii_flags if f.startswith(("name:", "phone:", "email:"))]

                if pii_only:
                    st.warning(f"⚠ PII в драфте — проверь вручную: {', '.join(pii_only)}")
                if quality_flags:
                    st.warning(f"⚠ Quality flags: {', '.join(quality_flags)}")

                st.success(
                    f"✓ Сохранён id={draft.id[:8]}… · ${draft.cost.cost_usd:.4f} · "
                    f"{len(draft.content)} chars · {draft.generation_duration_ms} ms · "
                    f"in={draft.cost.tokens_input} out={draft.cost.tokens_output} "
                    f"cache_w={draft.cost.cache_creation_tokens} cache_r={draft.cost.cache_read_tokens}"
                )

                col_ok, col_no, col_again = st.columns(3)
                with col_ok:
                    if st.button("✓ Одобрить", key="approve_now"):
                        update_status(conn, draft.id, status="approved", reviewed_by="UI")
                        st.toast("Одобрено")
                with col_no:
                    if st.button("✗ Отклонить", key="reject_now"):
                        update_status(conn, draft.id, status="rejected", reviewed_by="UI")
                        st.toast("Отклонено")
                with col_again:
                    st.caption("Ещё вариант — снова нажми 🚀 (тот же конфиг, anti-repeat активен)")


# --- Tab: 📋 Черновики ---
with tab_drafts:
    st.subheader("Черновики")
    f_col1, f_col2, f_col3, f_col4 = st.columns(4)
    with f_col1:
        f_status = st.selectbox("Статус", ["—", "draft", "approved", "rejected", "failed", "published"],
                                key="drafts_status")
    with f_col2:
        f_voice = st.selectbox("Голос", ["—"] + list_voice_profiles(), key="drafts_voice")
    with f_col3:
        f_channel = st.selectbox("Канал", ["—"] + list_channels(), key="drafts_channel")
    with f_col4:
        f_segment = st.selectbox("Сегмент", ["—"] + list_segments(), key="drafts_segment")

    drafts = list_drafts(
        conn,
        status=None if f_status == "—" else f_status,
        voice_profile=None if f_voice == "—" else f_voice,
        channel=None if f_channel == "—" else f_channel,
        segment=None if f_segment == "—" else f_segment,
        limit=50,
    )

    total_cost = sum(float(d["cost_usd"] or 0) for d in drafts)
    st.caption(f"Найдено: {len(drafts)} · общая стоимость: ${total_cost:.4f}")

    if not drafts:
        st.info("По фильтрам ничего не найдено.")
    for d in drafts:
        seg = d["segment_slug"] or "—"
        stage = d["hunt_stage"] if d["hunt_stage"] is not None else "—"
        hint = (d["topic_hint"] or "")[:60]
        header = (f"**{d['status']}** · {d['voice_profile_slug']} × "
                  f"{d['channel_slug']} × {d['content_form_slug']} · "
                  f"seg={seg} stage={stage} · ${d['cost_usd'] or 0:.4f} · "
                  f"{d['created_at'].strftime('%m-%d %H:%M')}"
                  + (f" · «{hint}…»" if hint else ""))
        with st.expander(header):
            full = load_draft(conn, d["id"])
            st.caption(f"id={d['id']}")
            if full["pii_flags"]:
                st.warning("Flags: " + ", ".join(full["pii_flags"]))
            st.markdown(full["content"])
            st.caption(
                f"model={full['model']} · prompt_ver={full['prompt_version']} · "
                f"tokens in={full['tokens_input']} out={full['tokens_output']} "
                f"cache_w={full['cache_creation_tokens']} cache_r={full['cache_read_tokens']} · "
                f"duration={full['generation_duration_ms']}ms"
            )

            notes_key = f"notes_{d['id']}"
            notes = st.text_input("Комментарий (опционально):", key=notes_key)
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                if st.button("✓ Одобрить", key=f"appr_{d['id']}"):
                    update_status(conn, d["id"], status="approved",
                                  reviewed_by="UI", review_notes=notes or None)
                    st.rerun()
            with c2:
                if st.button("✗ Отклонить", key=f"rej_{d['id']}"):
                    update_status(conn, d["id"], status="rejected",
                                  reviewed_by="UI", review_notes=notes or None)
                    st.rerun()
            with c3:
                if st.button("📤 Опубликовать", key=f"pub_{d['id']}"):
                    update_status(conn, d["id"], status="published", reviewed_by="UI")
                    st.rerun()
            with c4:
                if st.button("↺ В draft", key=f"rev_{d['id']}"):
                    update_status(conn, d["id"], status="draft", reviewed_by="UI")
                    st.rerun()
