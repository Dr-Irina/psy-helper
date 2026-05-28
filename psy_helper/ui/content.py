"""Страница «🎨 Контент» — генератор + работа с черновиками + источники + заметки.

Tabs: Генератор · Черновики · Источники · Замечания
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from psy_helper.content_gen.annotations import (
    STATUS_LABELS,
    VERDICT_LABELS,
    count_open_for,
    delete_annotation,
    list_annotations,
    update_annotation_status,
)
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
    load_forbidden_topics,
    load_lexicon,
    load_psycho_type,
    load_segment,
    load_voice_doc,
    load_voice_profile,
)
from psy_helper.content_gen.storage import (
    list_drafts,
    load_draft,
    update_status,
)

from . import helpers as H

SOURCE_TYPE_LABELS = {
    "all": "все типы",
    "voice_profile": "🎙 Voice profile",
    "voice_doc": "📜 Voice doc",
    "lexicon_question": "❓ Вопрос (lexicon)",
    "lexicon_metaphor": "🌀 Метафора (lexicon)",
    "forbidden_topic": "🚫 Топик",
    "forbidden_phrase_group": "🚫 Группа фраз",
    "segment": "👤 Сегмент",
    "psycho_type": "🧠 Психотип",
    "channel": "📡 Канал",
    "content_form": "🧱 Форма",
}


def render() -> None:
    conn = H.get_conn()

    st.title("🎨 Контент")

    tab_gen, tab_drafts, tab_sources, tab_notes = st.tabs(
        ["🚀 Генератор", "📋 Черновики", "📚 Источники", "💭 Замечания"]
    )

    _render_generator(conn, tab_gen)
    _render_drafts(conn, tab_drafts)
    _render_sources(conn, tab_sources)
    _render_notes(conn, tab_notes)


# ─── Tab: Генератор ──────────────────────────────────────────────────────────

def _render_generator(conn, tab) -> None:
    with tab:
        st.caption(
            "Собираем черновик из 5 настроек: голос × сегмент × психотип × канал × форма. "
            f"Лимит: {H.RATE_LIMIT_MAX} генераций / {H.RATE_LIMIT_WINDOW//60} минут."
        )

        col_a, col_b = st.columns(2, gap="medium")
        with col_a:
            vps = list_voice_profiles()
            voice_slug = st.selectbox(
                "Голос", vps,
                index=vps.index("anna_product") if "anna_product" in vps else 0,
                key="gen_voice",
            )
            chs = list_channels()
            channel_slug = st.selectbox(
                "Канал", chs,
                index=chs.index("tg_post") if "tg_post" in chs else 0,
                key="gen_channel",
            )
            fms = list_content_forms()
            form_slug = st.selectbox(
                "Нарративная форма", fms,
                index=fms.index("storytelling") if "storytelling" in fms else 0,
                key="gen_form",
            )
        with col_b:
            segs = ["—"] + list_segments()
            segment_slug = st.selectbox(
                "Сегмент (опционально)", segs,
                index=segs.index("tired_wife") if "tired_wife" in segs else 0,
                key="gen_segment",
            )
            pts = ["—"] + list_psycho_types()
            pt_slug = st.selectbox(
                "Психотип (опционально)", pts,
                index=pts.index("patient") if "patient" in pts else 0,
                key="gen_pt",
            )
            hunt_stage = st.select_slider(
                "Ступень Ханта", options=[None, 1, 2, 3, 4, 5],
                value=2, format_func=lambda v: "—" if v is None else str(v),
                key="gen_stage",
            )

        topics_choice = st.multiselect(
            "Топики (фильтр корпуса)",
            ["marriage", "partnership", "children", "teens", "confidence",
             "personal_effectiveness", "finance", "communication", "general"],
            default=["marriage"], key="gen_topics",
        )
        topic_hint = st.text_input(
            "Конкретная тема (опционально)",
            placeholder="например: границы в супружестве",
            key="gen_hint",
        )

        # Свёрнутая панель «что выбрано» — на случай если нужно перепроверить
        with st.expander("ℹ Что значат выбранные настройки"):
            _vp = load_voice_profile(voice_slug)
            st.markdown(
                f"**Голос `{voice_slug}`:** {_vp.register_} · "
                f"обращение «{_vp.form_of_address}» · "
                f"мат: {'разрешён' if _vp.mat_allowed else 'нет'}"
            )
            if segment_slug != "—":
                _seg = load_segment(segment_slug)
                st.markdown(f"**Сегмент `{segment_slug}` — {_seg.name}:** {(_seg.situation or '').strip()[:200]}")
            if pt_slug != "—":
                _pt = load_psycho_type(pt_slug)
                st.markdown(f"**Психотип `{pt_slug}` — {_pt.name}:** мотиватор: {_pt.motivator}")
            _ch = load_channel(channel_slug)
            L = _ch.length
            length_text = (
                f"{L.min_chars or '?'}–{L.max_chars} символов" if L.max_chars
                else f"{L.duration_seconds_min}–{L.duration_seconds_max} сек" if L.duration_seconds_max
                else "—"
            )
            st.markdown(f"**Канал `{channel_slug}`:** {length_text} · модель {_ch.preferred_model}")
            _cf = load_content_form(form_slug)
            st.markdown(f"**Форма `{form_slug}`:** мин. фирменных фраз = {_cf.lexicon_min}")

        with st.expander("⚙ Доп. настройки"):
            model_override = st.radio(
                "Модель", options=[None, "claude-sonnet-4-6", "claude-haiku-4-5"],
                format_func=lambda m: "из канала" if m is None else m.split("-")[1].capitalize(),
                horizontal=True, key="gen_model",
            )

        # Cost panel
        cum = st.session_state.get("cumulative_cost", 0.0)
        cum_n = st.session_state.get("cumulative_count", 0)
        st.caption(f"💰 За сессию: ${cum:.4f} · {cum_n} генераций")

        if st.button("🚀 Сгенерировать", type="primary", key="gen_run"):
            allowed, wait = H.check_rate_limit()
            if not allowed:
                st.error(f"Лимит ({H.RATE_LIMIT_MAX}/{H.RATE_LIMIT_WINDOW//60}мин). Подожди ~{wait} сек.")
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
                placeholder = st.empty()
                placeholder.info("Подбираю материал из корпуса и стартую…")
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

                H.record_generation()
                draft = draft_holder.get("draft")
                if draft:
                    st.session_state["cumulative_cost"] = cum + draft.cost.cost_usd
                    st.session_state["cumulative_count"] = cum_n + 1
                    pii_only = [f for f in draft.pii_flags
                                if f.startswith(("name:", "phone:", "email:"))]
                    quality = [f for f in draft.pii_flags
                               if not f.startswith(("name:", "phone:", "email:"))]
                    if pii_only:
                        st.warning(f"⚠ PII — проверь вручную: {', '.join(pii_only)}")
                    if quality:
                        st.warning(f"⚠ Quality flags: {', '.join(quality)}")
                    st.success(
                        f"✓ id={draft.id[:8]}… · ${draft.cost.cost_usd:.4f} · "
                        f"{len(draft.content)} chars · {draft.generation_duration_ms} ms"
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
                        st.caption("Ещё вариант — снова нажми 🚀 (anti-repeat подхватит этот draft).")


# ─── Tab: Черновики ─────────────────────────────────────────────────────────

def _render_drafts(conn, tab) -> None:
    with tab:
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
            header = (
                f"**{d['status']}** · {d['voice_profile_slug']} × "
                f"{d['channel_slug']} × {d['content_form_slug']} · "
                f"seg={seg} stage={stage} · ${d['cost_usd'] or 0:.4f} · "
                f"{d['created_at'].strftime('%m-%d %H:%M')}"
                + (f" · «{hint}…»" if hint else "")
            )
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
                with st.expander("🧩 Конфиг этого драфта (snapshot)"):
                    st.json(full["config_snapshot"], expanded=False)
                with st.expander("🔗 Provenance — ссылки на корпус"):
                    prov = full["provenance"] or {}
                    if prov:
                        for tag, uuid in list(prov.items())[:30]:
                            st.markdown(f"- `{tag}` → `{uuid}`")
                        if len(prov) > 30:
                            st.caption(f"… ещё {len(prov) - 30}")
                    else:
                        st.caption("(нет ссылок)")

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


# ─── Tab: Источники ──────────────────────────────────────────────────────────

def _render_sources(conn, tab) -> None:
    with tab:
        st.caption(
            "Все материалы, на которых строятся черновики. "
            "На каждой карточке можно оставить заметку для следующей версии."
        )
        section = st.radio(
            "Раздел",
            ["🎙 Voice profiles", "📜 Voice documents", "💬 Стиль",
             "👤 Аудитория", "📡 Каналы", "🧱 Формы"],
            horizontal=True, key="sources_section",
        )

        if section.startswith("🎙"):
            _src_voice_profiles(conn)
        elif section.startswith("📜"):
            _src_voice_docs(conn)
        elif section.startswith("💬"):
            _src_style(conn)
        elif section.startswith("👤"):
            _src_audience(conn)
        elif section.startswith("📡"):
            _src_channels(conn)
        elif section.startswith("🧱"):
            _src_forms(conn)


def _src_voice_profiles(conn) -> None:
    for slug in list_voice_profiles():
        vp = load_voice_profile(slug)
        placeholder = " (PLACEHOLDER)" if vp.placeholder else ""
        open_n = count_open_for(conn, "voice_profile", slug)
        badge = f" · 💭{open_n}" if open_n else ""
        with st.expander(f"**{vp.name}** · `{slug}`{placeholder}{badge}"):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"- **Автор:** {vp.author}")
                st.markdown(f"- **Регистр:** {vp.register_}")
                st.markdown(f"- **Обращение:** «{vp.form_of_address}»")
                st.markdown(f"- **Мат:** {'разрешён' if vp.mat_allowed else 'нет'}")
            with col2:
                st.markdown(f"- **Voice doc:** `{vp.sources.voice_doc}`")
                st.markdown(f"- **Lexicon:** `{vp.sources.lexicon}`")
                st.markdown(f"- **Raw quotes:** `{vp.sources.raw_quotes.path}`")
            if getattr(vp, "description", None):
                st.info(str(vp.description).strip())
            if vp.antipatterns:
                st.markdown("**Антипаттерны:** " + ", ".join(f"«{p}»" for p in vp.antipatterns))
            if vp.term_replacements:
                st.markdown("**Замены терминов:** " + ", ".join(
                    f"«{k}» → " + ("УДАЛИТЬ" if v is None else f"«{v}»")
                    for k, v in vp.term_replacements.items()
                ))
            if vp.joint_markers:
                st.markdown("**Joint markers:**\n" + "\n".join(f"- {m}" for m in vp.joint_markers))
            H.annotation_widget(conn, "voice_profile", slug)


def _src_voice_docs(conn) -> None:
    st.caption(
        "Voice document = «семантика голоса автора» — принципы, red lines, техники."
    )
    VD_DIR = Path("data/voice_document")
    files = sorted(VD_DIR.glob("*.md"))
    if not files:
        st.info("В `data/voice_document/` нет .md файлов.")
        return
    chosen = st.selectbox("Voice document", files, format_func=lambda p: p.name,
                          key="sources_vd_pick")
    text = load_voice_doc(str(chosen))
    open_n = count_open_for(conn, "voice_doc", chosen.name)
    badge = f" · 💭 {open_n} открытых заметок" if open_n else ""
    st.caption(f"`{chosen}` · {len(text):,} chars{badge}")
    st.markdown(text)
    H.annotation_widget(
        conn, "voice_doc", chosen.name,
        label="💬 Оставить заметку к этому voice doc",
    )


def _src_style(conn) -> None:
    lex = load_lexicon()
    forb = load_forbidden_topics()
    q_count = len(lex.get("questions", []))
    m_count = len(lex.get("metaphors", []))
    st.caption(
        f"Lexicon: **{q_count} фирменных вопросов** + **{m_count} метафор**. "
        f"Forbidden v{forb.get('version', '?')}."
    )
    sub = st.radio("Что смотрим", ["Вопросы", "Метафоры", "Запрещённое"],
                   horizontal=True, key="sources_style_sub")
    if sub == "Вопросы":
        search = st.text_input("Поиск", key="lex_q_search")
        items = lex.get("questions", [])
        if search:
            s = search.lower()
            items = [q for q in items if s in q.get("phrase", "").lower()
                     or s in q.get("description", "").lower()]
        st.caption(f"Показано: {len(items)}")
        for q in items:
            open_n = count_open_for(conn, "lexicon_question", q["phrase"])
            badge = f" · 💭{open_n}" if open_n else ""
            with st.expander(f"**«{q['phrase']}»** · упомянуто {q.get('mentions', '?')} раз{badge}"):
                st.write(q.get("description", ""))
                H.annotation_widget(conn, "lexicon_question", q["phrase"],
                                    key_suffix=f"lq_{hash(q['phrase']) & 0xffff}")
    elif sub == "Метафоры":
        search = st.text_input("Поиск", key="lex_m_search")
        items = lex.get("metaphors", [])
        if search:
            s = search.lower()
            items = [m for m in items if s in m.get("phrase", "").lower()
                     or s in m.get("description", "").lower()]
        st.caption(f"Показано: {len(items)}")
        for m in items[:150]:
            open_n = count_open_for(conn, "lexicon_metaphor", m["phrase"])
            badge = f" · 💭{open_n}" if open_n else ""
            with st.expander(f"**«{m['phrase']}»** · упомянуто {m.get('mentions', '?')} раз{badge}"):
                st.write(m.get("description", ""))
                H.annotation_widget(conn, "lexicon_metaphor", m["phrase"],
                                    key_suffix=f"lm_{hash(m['phrase']) & 0xffff}")
        if len(items) > 150:
            st.caption(f"… скрыто ещё {len(items) - 150}. Уточни поиск.")
    else:
        st.markdown("### Запрещённые ТЕМЫ (топики)")
        for t in forb.get("topics", []):
            open_n = count_open_for(conn, "forbidden_topic", t["id"])
            badge = f" · 💭{open_n}" if open_n else ""
            with st.expander(f"**{t['label']}** · `{t['id']}`{badge}"):
                st.markdown(f"**Причина:** {t.get('reason', '—')}")
                if t.get("examples"):
                    st.markdown("**Примеры:** " + ", ".join(f"«{e}»" for e in t["examples"]))
                H.annotation_widget(conn, "forbidden_topic", t["id"])
        st.markdown("### Запрещённые ФРАЗЫ (антипаттерны языка)")
        for g in forb.get("phrases", []):
            applies = ", ".join(g.get("applies_to", []))
            open_n = count_open_for(conn, "forbidden_phrase_group", g["id"])
            badge = f" · 💭{open_n}" if open_n else ""
            with st.expander(f"**{g['label']}** · `{g['id']}` · применяется к: {applies}{badge}"):
                st.markdown(f"**Причина:** {g.get('reason', '—')}")
                st.markdown("\n".join(f"- «{p}»" for p in g.get("phrases", [])))
                H.annotation_widget(conn, "forbidden_phrase_group", g["id"])


def _src_audience(conn) -> None:
    st.caption("Сегменты — КОМУ. Психотипы — ЧЕМ цеплять.")
    sub = st.radio("Что", ["Сегменты", "Психотипы"], horizontal=True, key="aud_sub")
    if sub == "Сегменты":
        for slug in list_segments():
            seg = load_segment(slug)
            tag = " 🌟 главный" if seg.priority == 1 else ""
            open_n = count_open_for(conn, "segment", slug)
            badge = f" · 💭{open_n}" if open_n else ""
            with st.expander(f"**{seg.name}** · `{slug}`{tag}{badge}"):
                if seg.situation:
                    st.info(seg.situation.strip())
                if seg.pain_phrases:
                    st.markdown("**Их слова о боли:** " + " / ".join(f"«{p}»" for p in seg.pain_phrases))
                if seg.objections:
                    st.markdown("**Возражения:** " + " / ".join(f"«{o}»" for o in seg.objections))
                if seg.main_message:
                    st.success(f"**Главное сообщение для них:**\n\n{seg.main_message.strip()}")
                if seg.main_psycho_types:
                    st.caption(f"Подходящие психотипы: {', '.join(seg.main_psycho_types)}")
                H.annotation_widget(conn, "segment", slug)
    else:
        for slug in list_psycho_types():
            pt = load_psycho_type(slug)
            tag = " 🌟 главный" if pt.priority == 1 else ""
            open_n = count_open_for(conn, "psycho_type", slug)
            badge = f" · 💭{open_n}" if open_n else ""
            with st.expander(f"**{pt.name}** · `{slug}`{tag}{badge}"):
                st.markdown(f"**Мотиватор:** {pt.motivator}")
                if pt.decision_speed:
                    st.markdown(f"**Скорость решения:** {pt.decision_speed}")
                if pt.attracts:
                    st.markdown("**Цепляет:** " + ", ".join(pt.attracts))
                if pt.repels:
                    st.markdown("**Отталкивает:** " + ", ".join(pt.repels))
                if pt.key_argument:
                    st.success(f"**Ключевой аргумент:** {pt.key_argument}")
                if pt.cta_examples:
                    st.markdown("**Примеры CTA:**\n" + "\n".join(f"- {c}" for c in pt.cta_examples))
                H.annotation_widget(conn, "psycho_type", slug)


def _src_channels(conn) -> None:
    for slug in list_channels():
        ch = load_channel(slug)
        open_n = count_open_for(conn, "channel", slug)
        badge = f" · 💭{open_n}" if open_n else ""
        with st.expander(f"**{ch.name}** · `{slug}` · модель: {ch.preferred_model.split('-')[1]}{badge}"):
            L = ch.length
            if L.max_chars:
                st.markdown(f"- **Длина:** {L.min_chars or '?'}–{L.max_chars} символов "
                            f"(оптимально {L.optimal_chars})")
            elif L.duration_seconds_max:
                st.markdown(f"- **Длительность:** {L.duration_seconds_min}–{L.duration_seconds_max} сек")
            st.markdown(f"- **Обращение по умолчанию:** «{ch.voice_form_default}»")
            st.markdown(f"- **CTA:** {ch.cta_required} ({ch.cta_style or 'без описания'})")
            if ch.hook_style:
                st.markdown(f"**Hook:** {ch.hook_style.strip()}")
            if ch.structure_hint:
                st.markdown("**Структурный совет:**")
                st.code(ch.structure_hint.strip(), language="markdown")
            H.annotation_widget(conn, "channel", slug)


def _src_forms(conn) -> None:
    for slug in list_content_forms():
        cf = load_content_form(slug)
        open_n = count_open_for(conn, "content_form", slug)
        badge = f" · 💭{open_n}" if open_n else ""
        with st.expander(f"**{cf.name}** · `{slug}` · мин. фирменных фраз: {cf.lexicon_min}{badge}"):
            if cf.structure_template:
                st.markdown("**Структура:**")
                st.code(cf.structure_template.strip(), language="markdown")
            if cf.hook_style:
                st.markdown(f"**Hook:** {cf.hook_style.strip()}")
            if cf.requires_hero:
                st.warning("⚠ Эта форма требует героя (анонимизированного)")
            if cf.notes:
                st.markdown("**Заметки:**\n" + "\n".join(f"- {n}" for n in cf.notes))
            H.annotation_widget(conn, "content_form", slug)


# ─── Tab: Замечания ──────────────────────────────────────────────────────────

def _render_notes(conn, tab) -> None:
    with tab:
        st.caption("Заметки на исходные материалы — для подготовки следующих версий.")

        fcol1, fcol2, fcol3 = st.columns(3)
        with fcol1:
            n_status = st.selectbox(
                "Статус", ["open", "all", "addressed", "wontfix"],
                format_func=lambda s: "все" if s == "all" else STATUS_LABELS.get(s, s),
                key="notes_status",
            )
        with fcol2:
            n_verdict = st.selectbox(
                "Вердикт", ["all"] + list(VERDICT_LABELS),
                format_func=lambda v: "все" if v == "all" else VERDICT_LABELS[v],
                key="notes_verdict",
            )
        with fcol3:
            n_stype = st.selectbox(
                "Тип источника", list(SOURCE_TYPE_LABELS),
                format_func=lambda k: SOURCE_TYPE_LABELS[k],
                key="notes_stype",
            )

        notes = list_annotations(
            conn,
            source_type=None if n_stype == "all" else n_stype,
            status=None if n_status == "all" else n_status,
            verdict=None if n_verdict == "all" else n_verdict,
            limit=300,
        )

        by_verdict = {v: sum(1 for n in notes if n["verdict"] == v) for v in VERDICT_LABELS}
        counts_line = " · ".join(
            f"{VERDICT_LABELS[v]}: **{by_verdict[v]}**" for v in VERDICT_LABELS if by_verdict[v]
        ) or "—"
        st.caption(f"Найдено: {len(notes)} · {counts_line}")

        if not notes:
            st.info("Заметок нет. На «📚 Источниках» есть форма «💬 Оставить заметку» на каждой карточке.")
        for a in notes:
            vlabel = VERDICT_LABELS.get(a["verdict"], a["verdict"])
            slabel = STATUS_LABELS.get(a["status"], a["status"])
            stype_label = SOURCE_TYPE_LABELS.get(a["source_type"], a["source_type"])
            header = (
                f"{vlabel} · {slabel} · {stype_label} · `{a['source_id']}` · "
                f"{a['created_at'].strftime('%m-%d %H:%M')}"
            )
            with st.expander(header):
                if a["line_anchor"]:
                    st.markdown(f"**Якорь:** «{a['line_anchor']}»")
                if a["comment"]:
                    st.markdown(f"**Комментарий:** {a['comment']}")
                if a["addressed_in_version"]:
                    st.caption(f"Применено в: {a['addressed_in_version']}")
                st.caption(f"id={a['id']} · author={a['author']}")

                if a["status"] == "open":
                    bcols = st.columns(4)
                    with bcols[0]:
                        if st.button("✅ применено", key=f"napp_{a['id']}"):
                            update_annotation_status(conn, a["id"], status="addressed")
                            st.rerun()
                    with bcols[1]:
                        if st.button("⊘ не править", key=f"nwont_{a['id']}"):
                            update_annotation_status(conn, a["id"], status="wontfix")
                            st.rerun()
                    with bcols[2]:
                        if st.button("🗑 удалить", key=f"ndel_{a['id']}"):
                            delete_annotation(conn, a["id"])
                            st.rerun()
                else:
                    if st.button("↺ Открыть заново", key=f"nreopen_{a['id']}"):
                        update_annotation_status(conn, a["id"], status="open")
                        st.rerun()
