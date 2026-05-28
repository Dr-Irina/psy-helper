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
        query = st.text_input(
            "Что найти?",
            placeholder="например: как слушать партнёра / что почитать / когда обращаться к терапевту",
            key="search_query",
        )

        with st.expander("⚙ Параметры поиска"):
            f_cols = st.columns([3, 1])
            with f_cols[0]:
                selected_types = st.multiselect(
                    "Фильтр по типам концептов (опционально)",
                    list(CONCEPT_TYPES.keys()),
                    default=[],
                    format_func=lambda t: f"{t} — {H.TYPE_LABELS.get(t, t)}",
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
            with st.spinner("Поиск…"):
                v = H.get_model().encode([f"query: {query}"], normalize_embeddings=True)[0]
                concepts = H.do_search_concepts(conn, query, v, selected_types or None, limit=depth)
                segments = H.do_search_segments(conn, query, v, limit=depth)
                lexicon = H.do_search_lexicon(conn, query, v, limit=depth)

            st.caption(
                f"Найдено: **{len(concepts)}** концептов · "
                f"**{len(lexicon)}** фирменных фраз · "
                f"**{len(segments)}** блоков лекций"
            )

            t_c, t_l, t_s = st.tabs([
                f"🧩 Концепты ({len(concepts)})",
                f"❓ Фразы Анны ({len(lexicon)})",
                f"📖 Блоки лекций ({len(segments)})",
            ])

            # ─── Концепты с группировкой по типам ────────────────────────
            with t_c:
                if not concepts:
                    st.info("Ничего не нашлось — попробуй другие слова.")
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
                if not segments:
                    st.info("Нет похожих блоков.")
                else:
                    _paginate(
                        key="page_seg",
                        items=segments,
                        section_label=None,
                        render=lambda s: _render_segment(s),
                    )
        else:
            st.info(
                "Задай вопрос как клиент или как Анна — своими словами. "
                "Гибридный поиск (BM25 + векторный) ищет одновременно "
                "по концептам, фирменным фразам и блокам лекций — каждый "
                "источник в своей вкладке."
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
    with st.expander(f"{c.name} · {c.score:.3f}{sources_part}"):
        st.write(c.description or "")
        if c.sources_count:
            with st.expander(f"📖 Откуда это (в {c.sources_count} блоках)"):
                blocks = H.concept_source_segments(conn, c.id)
                # группируем по лекции; внутри лекции сортируем по timestamp
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
