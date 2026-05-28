"""Страница «📚 База знаний» — что есть в корпусе Анны.

Tabs: Поиск · По типам · По лекциям · Похожие
"""
from __future__ import annotations

import streamlit as st

from psy_helper.taxonomy import CONCEPT_TYPES

from . import helpers as H


def render() -> None:
    conn = H.get_conn()
    stats = H.db_stats(conn)

    st.title("📚 База знаний — метод Анны")
    st.caption(
        f"{stats['lectures']} лекций · {stats['segments']} смысловых блоков · "
        f"{stats['concepts']} концептов"
    )

    tab_search, tab_types, tab_lectures, tab_related = st.tabs(
        ["🔍 Поиск", "🧩 По типам", "📖 По лекциям", "🔗 Похожие"]
    )

    # ─── Поиск ─────────────────────────────────────────────────────────────
    with tab_search:
        selected_types = st.multiselect(
            "Фильтр по типам (опционально)",
            list(CONCEPT_TYPES.keys()),
            default=[],
            format_func=lambda t: f"{t} — {H.TYPE_LABELS.get(t, t)}",
            key="search_types",
        )
        query = st.text_input(
            "Что найти?",
            placeholder="например: как слушать партнёра / что почитать / когда обращаться к терапевту",
        )

        if query:
            with st.spinner("Поиск…"):
                v = H.get_model().encode([f"query: {query}"], normalize_embeddings=True)[0]
                concepts = H.do_search_concepts(conn, query, v, selected_types or None)
                segments = H.do_search_segments(conn, query, v)
                lexicon = H.do_search_lexicon(conn, query, v, limit=10)

            col_c, col_l, col_s = st.columns([3, 2, 2], gap="large")
            with col_c:
                st.subheader(f"🧩 Концепты ({len(concepts)})")
                if not concepts:
                    st.info("Ничего не нашлось — попробуй другие слова.")
                for c in concepts:
                    sources_part = f" · в {c.sources_count} блоках" if c.sources_count else ""
                    with st.expander(f"**{c.name}** · _{c.type}_ · {c.score:.3f}{sources_part}"):
                        st.write(c.description or "")
            with col_l:
                st.subheader(f"❓ Фразы Анны ({len(lexicon)})")
                st.caption("Её фирменные вопросы и метафоры из lexicon")
                if not lexicon:
                    st.info("Нет похожих фраз.")
                for li in lexicon:
                    icon = "❓" if li.kind == "question" else "🌀"
                    mentions = f" · упомянуто {li.mentions} раз" if li.mentions else ""
                    with st.expander(f"{icon} **«{li.phrase}»** · {li.score:.3f}{mentions}"):
                        st.write(li.description or "")
            with col_s:
                st.subheader(f"📖 Блоки лекций ({len(segments)})")
                for s in segments:
                    ts = H.fmt_ts_range(s.start_ts, s.end_ts)
                    with st.expander(f"**{s.title}** · _{ts}_ · {s.score:.3f}"):
                        st.write(s.summary or "")
                        st.caption(f"📁 {H.lecture_name(s.source_file)}")
        else:
            st.info(
                "Задай вопрос как клиент или как Анна — своими словами. "
                "Гибридный поиск: BM25 (по словам) + векторный (по смыслу) "
                "по концептам, лексикону (фирменные фразы) и блокам лекций."
            )

    # ─── По типам ──────────────────────────────────────────────────────────
    with tab_types:
        counts = H.type_counts(conn)
        options = list(CONCEPT_TYPES.keys())
        chosen_type = st.selectbox(
            "Тип концептов",
            options,
            format_func=lambda t: f"{H.TYPE_LABELS.get(t, t)} ({counts.get(t, 0)})",
            key="browse_type",
        )
        items = H.concepts_of_type(conn, chosen_type)
        st.caption(
            f"{len(items)} концептов типа _{H.TYPE_LABELS.get(chosen_type, chosen_type)}_. "
            f"Сортировка: по количеству источников, затем по алфавиту."
        )
        for name, description, sources_count in items:
            sources_part = f" · в {sources_count} блоках" if sources_count else ""
            with st.expander(f"**{name}**{sources_part}"):
                st.write(description or "")

    # ─── По лекциям ────────────────────────────────────────────────────────
    with tab_lectures:
        lectures = H.all_lectures(conn)
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
                for seg_id, title, summary, start_ts, end_ts in H.lecture_segments(conn, raw_id):
                    ts = H.fmt_ts_range(start_ts, end_ts)
                    with st.expander(f"**{title}** · _{ts}_"):
                        st.write(summary or "")

            with col2:
                st.subheader(f"🧩 Концепты ({n_cons})")
                grouped = {}
                for cid, name, ctype, description in H.lecture_concepts(conn, raw_id):
                    grouped.setdefault(ctype, []).append((name, description))
                for ctype in CONCEPT_TYPES.keys():
                    if ctype not in grouped:
                        continue
                    items = grouped[ctype]
                    with st.expander(f"**{H.TYPE_LABELS.get(ctype, ctype)}** ({len(items)})"):
                        for name, description in items:
                            st.markdown(f"- **{name}** — {description or ''}")

    # ─── Похожие ───────────────────────────────────────────────────────────
    with tab_related:
        all_names = H.all_concept_names(conn)
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
                for sid, sname, stype, sdesc, sim, sources_count in H.similar_concepts(conn, cid):
                    sp = f" · в {sources_count} блоках" if sources_count else ""
                    with st.expander(f"**{sname}** · _{stype}_ · sim {sim:.3f}{sp}"):
                        st.write(sdesc or "")

            with col2:
                st.subheader("📍 Из тех же блоков")
                st.caption(
                    "Концепты, которые встречаются в одних и тех же блоках лекций — "
                    "Анна обсуждает их вместе"
                )
                for sid, sname, stype, shared in H.co_occurring_concepts(conn, cid):
                    with st.expander(f"**{sname}** · _{stype}_ · {shared} общих блоков"):
                        pass
