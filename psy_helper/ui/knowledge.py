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

    tab_search, tab_lectures, tab_related = st.tabs(
        ["🔍 Поиск и просмотр", "📖 По лекциям", "🔗 Похожие"]
    )

    # ─── Поиск ─────────────────────────────────────────────────────────────
    with tab_search:
        query = st.text_input(
            "Что найти?",
            placeholder="например: как слушать партнёра / что почитать / когда обращаться к терапевту",
            key="search_query",
        )

        with st.expander("⚙ Параметры поиска"):
            counts = H.type_counts(conn)
            f_cols = st.columns([3, 1])
            with f_cols[0]:
                selected_types = st.multiselect(
                    "Фильтр по типам концептов (пусто = все типы)",
                    list(CONCEPT_TYPES.keys()),
                    default=[],
                    format_func=lambda t: f"{H.TYPE_LABELS.get(t, t)} ({counts.get(t, 0)})",
                    key="search_types",
                )
            with f_cols[1]:
                depth = st.number_input(
                    "Глубина поиска", min_value=10, max_value=200, value=50, step=10,
                    key="search_depth",
                    help="Сколько результатов поднимать из БД по каждому источнику. "
                         "Технический потолок ~100 (после ~50 начинается шум).",
                )

        if query:
            mode = "search"
            with st.spinner("Поиск…"):
                v = H.get_model().encode([f"query: {query}"], normalize_embeddings=True)[0]
                concepts = H.do_search_concepts(conn, query, v, selected_types or None, limit=depth)
                segments = H.do_search_segments(conn, query, v, limit=depth)
                lexicon = H.do_search_lexicon(conn, query, v, limit=depth)
        elif selected_types:
            mode = "browse"
            # Запрос пустой + выбран хотя бы один тип → каталог: все концепты этих типов
            concepts = H.browse_concepts_by_types(conn, selected_types)
            segments = []
            lexicon = []
        else:
            mode = "idle"
            concepts, segments, lexicon = [], [], []

        if mode == "idle":
            st.info(
                "Введи запрос — найду релевантные концепты, фирменные фразы и блоки лекций "
                "(гибридный BM25 + векторный поиск).\n\n"
                "Или выбери тип в «⚙ Параметры поиска» и оставь запрос пустым — "
                "увидишь все концепты этого типа без ранжирования."
            )
            cstats = H.db_stats(conn)
            st.caption(
                f"В корпусе: **{cstats['concepts']}** концептов · "
                f"**414** фирменных фраз Анны (вопросы + метафоры) · "
                f"**{cstats['segments']}** смысловых блоков из {cstats['lectures']} лекций."
            )
        elif mode == "search":
            st.caption(
                f"Найдено по запросу «{query}»: **{len(concepts)}** концептов · "
                f"**{len(lexicon)}** фирменных фраз · "
                f"**{len(segments)}** блоков лекций"
            )
        elif mode == "browse":
            type_labels = ", ".join(H.TYPE_LABELS.get(t, t) for t in selected_types)
            st.caption(
                f"Каталог · типы: **{type_labels}** · "
                f"всего: **{len(concepts)}** концептов "
                f"(сортировка по количеству источников)"
            )

        if mode == "idle":
            # Не показываем результаты-вкладки до запроса/фильтра.
            pass
        else:
            t_c, t_l, t_s = st.tabs([
                f"🧩 Концепты ({len(concepts)})",
                f"❓ Фразы Анны ({len(lexicon)})",
                f"📖 Блоки лекций ({len(segments)})",
            ])

            # ─── Концепты с группировкой по типам ────────────────────────
            with t_c:
                if not concepts:
                    st.info("Ничего не нашлось — попробуй другие слова или другой тип.")
                else:
                    TYPE_EMOJI = {
                        "term": "📖", "technique": "🛠", "claim": "💬", "warning": "⚠",
                        "recommendation": "📚", "exercise": "🏋", "question": "❓",
                        "metaphor": "🌀", "example": "📌",
                    }
                    grouped: dict[str, list] = {}
                    for c in concepts:
                        grouped.setdefault(c.type, []).append(c)
                    for t in CONCEPT_TYPES.keys():
                        items = grouped.get(t)
                        if not items:
                            continue
                        emoji = TYPE_EMOJI.get(t, "🧩")
                        label = H.TYPE_LABELS.get(t, t)
                        _paginate(
                            key=f"page_c_{t}",
                            items=items,
                            section_label=f"{emoji} {label} ({len(items)})",
                            render=lambda c, _conn=conn: _render_concept(c, _conn),
                        )

            # ─── Фразы Анны ───────────────────────────────────────────────
            with t_l:
                if mode == "browse":
                    st.caption("Чтобы увидеть релевантные фирменные фразы — введи запрос.")
                else:
                    st.caption("Её фирменные вопросы и метафоры из lexicon.json")
                    if not lexicon:
                        st.info("Нет похожих фраз.")
                    else:
                        _paginate(
                            key="page_lex",
                            items=lexicon,
                            section_label=None,
                            render=lambda li: _render_lexicon(li),
                        )

            # ─── Блоки лекций ─────────────────────────────────────────────
            with t_s:
                if mode == "browse":
                    st.caption("Чтобы увидеть релевантные блоки лекций — введи запрос.")
                else:
                    if not segments:
                        st.info("Нет похожих блоков.")
                    else:
                        _paginate(
                            key="page_seg",
                            items=segments,
                            section_label=None,
                            render=lambda s: _render_segment(s),
                        )

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
                for seg_id, title, summary, text, start_ts, end_ts in H.lecture_segments(conn, raw_id):
                    ts = H.fmt_ts_range(start_ts, end_ts)
                    with st.expander(f"**{title}** · _{ts}_"):
                        if summary:
                            st.write(summary)
                        with st.expander("Показать оригинальный текст"):
                            st.write(text or "")

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

            # Карточка выбранного концепта — что это вообще
            target = H.get_concept(conn, cid)
            if target:
                _t_id, t_name, t_type, t_desc, t_sources = target
                sources_part = f" · в {t_sources} блоках" if t_sources else ""
                st.markdown(f"### 📌 {t_name}")
                st.caption(f"_{t_type}_{sources_part}")
                if t_desc:
                    st.info(t_desc)
                if t_sources:
                    with st.expander(f"📖 Откуда это (в {t_sources} блоках)"):
                        _render_concept_source_blocks(H.concept_source_segments(conn, cid))
                st.divider()

            col1, col2 = st.columns(2, gap="large")
            with col1:
                st.subheader("🔗 По смыслу")
                st.caption("Близкие по эмбеддингу — обычно связанная тематика")
                for sid, sname, stype, sdesc, sim, sources_count in H.similar_concepts(conn, cid):
                    sp = f" · в {sources_count} блоках" if sources_count else ""
                    with st.expander(f"**{sname}** · _{stype}_ · sim {sim:.3f}{sp}"):
                        st.write(sdesc or "")
                        if sources_count:
                            with st.expander(f"📖 Откуда это (в {sources_count} блоках)"):
                                _render_concept_source_blocks(H.concept_source_segments(conn, sid))

            with col2:
                st.subheader("📍 Из тех же блоков")
                st.caption(
                    "Концепты, которые встречаются в одних и тех же блоках лекций — "
                    "Анна обсуждает их вместе"
                )
                for sid, sname, stype, sdesc, shared in H.co_occurring_concepts(conn, cid):
                    with st.expander(f"**{sname}** · _{stype}_ · {shared} общих блоков"):
                        st.write(sdesc or "")
                        with st.expander(f"📍 Конкретно какие блоки общие ({shared})"):
                            _render_concept_source_blocks(
                                H.shared_segments_between(conn, cid, sid),
                            )


# ─── Пагинация и рендереры результатов ─────────────────────────────────────

_PAGE_SIZE = 15


def _paginate(*, key: str, items: list, section_label: str | None, render) -> None:
    """Показать items пачкой по _PAGE_SIZE, с кнопкой «Показать ещё».

    section_label = None → без под-заголовка (одна большая секция).
    """
    if section_label:
        st.markdown(f"**{section_label}**")
    shown = st.session_state.get(key, _PAGE_SIZE)
    for item in items[:shown]:
        render(item)
    if shown < len(items):
        cols = st.columns([3, 1])
        cols[0].caption(f"Показано {min(shown, len(items))} из {len(items)}")
        if cols[1].button(f"Показать ещё {min(_PAGE_SIZE, len(items) - shown)}",
                          key=f"more_{key}"):
            st.session_state[key] = shown + _PAGE_SIZE
            st.rerun()
    else:
        st.caption(f"Показаны все {len(items)}")


def _render_concept(c, conn) -> None:
    sources_part = f" · в {c.sources_count} блоках" if c.sources_count else ""
    score_part = f" · {c.score:.3f}" if c.score else ""
    with st.expander(f"{c.name}{score_part}{sources_part}"):
        st.write(c.description or "")
        if c.sources_count:
            with st.expander(f"📖 Откуда это (в {c.sources_count} блоках)"):
                _render_concept_source_blocks(H.concept_source_segments(conn, c.id))


def _render_concept_source_blocks(blocks: list) -> None:
    """Сгруппировать блоки по лекции, отсортировать по таймкоду, отрендерить.

    Принимает список строк (segment_id, raw_id, title, summary, text, start_ts, end_ts, source_file).
    Используется в Поиске и в «Похожих».
    """
    by_lecture: dict[str, list] = {}
    for row in blocks:
        _sid, _rid, _t, _s, _txt, st_ts, end_ts, src = row
        by_lecture.setdefault(src, []).append(row)
    for src, items in by_lecture.items():
        items.sort(key=lambda r: r[5] or 0)
        lec = H.lecture_name(src)
        st.markdown(f"#### 📁 {lec}")
        for _sid, _rid, title, summary, text, st_ts, end_ts, _src in items:
            ts = H.fmt_ts_range(st_ts or 0, end_ts or 0)
            head = f"**{ts}**" + (f" · _{title}_" if title else "")
            st.markdown(head)
            if summary:
                st.caption(summary)
            with st.expander("Показать оригинальный текст"):
                st.write(text)


def _render_lexicon(li) -> None:
    icon = "❓" if li.kind == "question" else "🌀"
    mentions = f" · упомянуто {li.mentions} раз" if li.mentions else ""
    with st.expander(f"{icon} «{li.phrase}» · {li.score:.3f}{mentions}"):
        st.write(li.description or "")


def _render_segment(s) -> None:
    from . import helpers as _H  # локальный импорт чтобы не дублировать
    ts = _H.fmt_ts_range(s.start_ts, s.end_ts)
    with st.expander(f"**{s.title}** · _{ts}_ · {s.score:.3f}"):
        st.write(s.summary or "")
        st.caption(f"📁 {_H.lecture_name(s.source_file)}")
