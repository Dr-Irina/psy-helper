"""Сборка system + user промтов из 5 слоёв конфига.

Архитектурное решение (Self-review C): НЕ один монолитный template, а:
    - BASE_TEMPLATE         — общие части (voice / audience / forbidden / retrieved material)
    - FORM_MODIFIERS[slug]  — короткие хвосты с специфичными инструкциями
      для конкретной content_form (storytelling: «начни с Setup», quiz: «дай
      нумерованный список ответов», и т.д.)

Финальный prompt = BASE_TEMPLATE + "\n\n" + FORM_MODIFIERS[form.slug]

Версионирование: PROMPT_VERSION меняется при любой правке шаблонов.
Это поле уходит в content_drafts.prompt_version → можно reproduce / A/B.
"""
from __future__ import annotations

import hashlib
from textwrap import dedent
from typing import Any

from psy_helper.content_gen.config import (
    Channel,
    ContentForm,
    GenerationConfig,
    PsychoType,
    Segment,
    VoiceProfile,
)
from psy_helper.content_gen.diversity import format_diversity_hint
from psy_helper.content_gen.few_shot import format_few_shot_block as _format_few_shot_in_prompt
from psy_helper.content_gen.retrieval import (
    RetrievalContext,
    format_concepts_for_prompt,
    format_segments_for_prompt,
)

# Версия меняется при любой правке шаблонов ниже.
# Формат: vMAJOR.MINOR.PATCH. Major = смена структуры, minor = новый блок,
# patch = текстовые правки.
PROMPT_VERSION = "v0.1.0"


# ─── BASE TEMPLATE ────────────────────────────────────────────────────────────

BASE_TEMPLATE = dedent("""\
    Ты помогаешь автору {author} писать контент для канала {channel_name}
    в {register} регистре.

    # ГОЛОС: {voice_name}
    {voice_description}

    Форма обращения к читателю: строго «{form_of_address}».
    Мат разрешён: {mat_allowed}.

    Сырые цитаты автора (для калибровки стиля — ТОН, не для копирования):
    {raw_quotes_block}

    Фирменные фразы автора (использовать минимум {lexicon_min}, без насилия в текст):
    {signature_phrases_block}

    # ЗАПРЕЩЕНО (повторение = брак):
    {forbidden_block}

    Терминологические правки в финальном тексте: {term_replacements}

    # АУДИТОРИЯ
    {audience_block}

    # КАНАЛ: {channel_name}
    {channel_block}

    # ФОРМА: {content_form_name}
    {content_form_block}

    # МАТЕРИАЛ ИЗ КОРПУСА АВТОРА

    Концепты (бери идеи отсюда, ставь footnotes [^cN]):
    {concepts_block}

    Смысловые блоки лекций (бери цитаты/иллюстрации, ставь [^sN]):
    {segments_block}

    # АНТИ-ПОВТОР
    {diversity_block}

    # ЭТАЛОННЫЕ ПРИМЕРЫ (одобренные ранее)
    {few_shot_block}

    # ПРАВИЛА ВЫВОДА
    1. Каждое сильное утверждение → footnote [^cN] или [^sN] из материала выше.
       Не выдумывай свои id — используй ТОЛЬКО теги, перечисленные в блоках.
    2. Минимум {lexicon_min} фирменных фраз автора (можно слегка адаптировать форму, не смысл).
    3. Форма обращения «{form_of_address}» — строго.
    4. Никаких запрещённых формулировок.
    5. Не выдумывай факты, кейсы, цифры — только то, что в корпусе.
    6. ЖЁСТКИЙ ЛИМИТ ДЛИНЫ (включая footnote-блок!): {length_hint}.
       Превысил — брак. Короче нижнего лимита — тоже брак.
       Считай символы по ходу. Если не уложишься — выкинь подробности, не CTA.

    Верни ТОЛЬКО готовый markdown-черновик с footnotes. Без преамбулы, без послесловий.
""")


# ─── FORM_MODIFIERS ────────────────────────────────────────────────────────────
# Хвостовые блоки с инструкциями, специфичными для конкретной content_form.
# Подмешиваются в конец BASE_TEMPLATE.

FORM_MODIFIERS: dict[str, str] = {
    "storytelling": dedent("""\
        ДОПОЛНИТЕЛЬНО (форма «storytelling»):
        - Структура: Setup → Catalyst → Confrontation → Insight → Resolution + moral
        - Герой ОБЯЗАТЕЛЬНО анонимизирован: возраст ± 3 года, имя обобщённое или без имени
        - Никаких реальных деталей клиентов — даже «придуманный» кейс должен быть
          статистически репрезентативным, не сплетней.
    """),
    "case_study": dedent("""\
        ДОПОЛНИТЕЛЬНО (форма «case_study»):
        - Структура: ситуация → разбор по методу → вывод
        - Имена и детали — обобщённые
        - В конце явный «применимо если…» / «не применимо если…»
    """),
    "tutorial": dedent("""\
        ДОПОЛНИТЕЛЬНО (форма «tutorial»):
        - Пронумерованные шаги, каждый — глагол + конкретное действие
        - В конце «как понять, что сработало»
    """),
    "tips_list": dedent("""\
        ДОПОЛНИТЕЛЬНО (форма «tips_list»):
        - 3–7 пунктов, каждый стоит сам по себе (можно вырезать любой без потери смысла)
        - Один пункт = одна мысль, без длинных раскрытий
    """),
    "opinion": dedent("""\
        ДОПОЛНИТЕЛЬНО (форма «opinion»):
        - Чёткая позиция автора в первых 2 строках
        - Аргументы + конкретный пример
        - Без отговорок «может быть» / «иногда» — позиция должна стоять
    """),
    "educational": dedent("""\
        ДОПОЛНИТЕЛЬНО (форма «educational»):
        - Тезис → объяснение «почему так» → как применить
        - Один концепт за пост, не вываливать всё
    """),
    "quote_card": dedent("""\
        ДОПОЛНИТЕЛЬНО (форма «quote_card»):
        - Одна цитата автора (5–15 слов). Точная, не пересказанная.
        - Footnote на источник [^sN] или [^cN] — обязателен.
        - Без обрамления и контекста — карточка стоит одна.
    """),
    "provocation": dedent("""\
        ДОПОЛНИТЕЛЬНО (форма «provocation»):
        - Парадокс / неожиданный угол / разрушенная установка в первой строке
        - В конце — поворот к реальности, не оставлять читателя в провокации
        - Без оскорблений и снисходительности.
    """),
    "quiz": dedent("""\
        ДОПОЛНИТЕЛЬНО (форма «quiz»):
        - Вопрос → нумерованные варианты (3–4) → инсайт по каждому варианту
        - Без правильных/неправильных ответов — это диагностика, не экзамен
    """),
    "metaphor_explain": dedent("""\
        ДОПОЛНИТЕЛЬНО (форма «metaphor_explain»):
        - Одна метафора (своя из корпуса автора [^cN]/[^sN] или органичная), развёрнутая на ~70% поста
        - В конце явный «перевод» метафоры на язык метода
    """),
}


# ─── Builders ──────────────────────────────────────────────────────────────────

def _format_raw_quotes(quotes: list[dict], voice: VoiceProfile) -> str:
    """Подобрать N цитат с учётом filter.remove_mat / max_quotes."""
    flt = voice.sources.raw_quotes.filter
    out: list[str] = []
    for q in quotes:
        text = q.get("text", "") if isinstance(q, dict) else str(q)
        if flt.remove_mat and any(m in text.lower() for m in ("пизд", "хуй", "ебан", "ёбан", "нахер", "блядь")):
            continue
        out.append(f"— {text.strip()[:300]}")
        if len(out) >= flt.max_quotes:
            break
    return "\n".join(out) if out else "(нет цитат)"


def _format_signature_phrases(retrieval, lexicon: dict, lexicon_min: int) -> str:
    if lexicon_min <= 0:
        return "(для этой формы не требуется)"
    # Приоритет — фирменные вопросы/метафоры, РЕТРИВНУТЫЕ под тему (варьируются по
    # теме → в каждом посте разные формулировки). Статичный lexicon — страховка.
    sig = getattr(retrieval, "signature", []) or []
    questions = [s["phrase"] for s in sig if s["type"] == "question"]
    metaphors = [s["phrase"] for s in sig if s["type"] == "metaphor"]
    if len(questions) < 3:
        questions += [q["phrase"] for q in lexicon.get("questions", [])[:5]]
    if len(metaphors) < 3:
        metaphors += [m["phrase"] for m in lexicon.get("metaphors", [])[:5]]
    questions = list(dict.fromkeys(questions))[:8]   # дедуп, сохраняя порядок
    metaphors = list(dict.fromkeys(metaphors))[:8]
    parts = []
    if questions:
        parts.append("ВОПРОСЫ автора: " + " / ".join(f"«{q}»" for q in questions))
    if metaphors:
        parts.append("МЕТАФОРЫ автора: " + " / ".join(f"«{m}»" for m in metaphors))
    return "\n".join(parts) or "(нет подходящих фирменных фраз)"


def _format_forbidden(voice: VoiceProfile, forbidden_topics: dict) -> str:
    lines: list[str] = []
    if voice.antipatterns:
        lines.append("Антипаттерны голоса: " + ", ".join(f"«{p}»" for p in voice.antipatterns))

    register_keys = {
        "лекторский": {"all", "lecturer"},
        "продуктовый": {"all", "product"},
    }.get(voice.register_, {"all"})
    if voice.slug == "joint_product":
        register_keys = register_keys | {"joint_product"}

    for group in forbidden_topics.get("phrases", []):
        if set(group.get("applies_to", [])) & register_keys:
            phrases = ", ".join(f"«{p}»" for p in group.get("phrases", []))
            lines.append(f"{group['label']}: {phrases}")

    for t in forbidden_topics.get("topics", []):
        if t["id"] in ("specific_clients", "diagnoses", "acute_states", "medical_advice"):
            lines.append(f"Темы запрещены ({t['label']}): {', '.join(t['examples'])}")

    return "\n".join(lines)


def _format_audience(seg: Segment | None, pt: PsychoType | None, hunt_stage: int | None) -> str:
    lines: list[str] = []
    if seg:
        lines.append(f"Сегмент «{seg.name}».")
        if seg.situation:
            lines.append(f"Ситуация: {seg.situation.strip()}")
        if seg.pain_phrases:
            lines.append("Их слова о боли: " + " / ".join(f"«{p}»" for p in seg.pain_phrases))
        if seg.objections:
            lines.append("Возражения: " + " / ".join(f"«{o}»" for o in seg.objections))
        if seg.main_message:
            lines.append(f"ГЛАВНОЕ СООБЩЕНИЕ ДЛЯ НИХ: {seg.main_message.strip()}")
    if pt:
        lines.append(f"\nПсихотип «{pt.name}».")
        if pt.motivator:
            lines.append(f"Мотиватор: {pt.motivator}")
        if pt.attracts:
            lines.append("Цепляет: " + ", ".join(pt.attracts))
        if pt.repels:
            lines.append("Отталкивает: " + ", ".join(pt.repels))
        if pt.key_argument:
            lines.append(f"Ключевой аргумент для них: {pt.key_argument}")
    if hunt_stage is not None:
        stage_names = {
            1: "не осознаёт проблемы",
            2: "осознаёт проблему, не ищет решение",
            3: "ищет варианты решения",
            4: "сравнивает конкретные решения",
            5: "готов к покупке",
        }
        lines.append(f"\nСтупень лестницы Ханта: {hunt_stage} — {stage_names.get(hunt_stage, '?')}")
    return "\n".join(lines) or "(аудитория не указана — пиши общее)"


def _format_channel(ch: Channel) -> str:
    lines = [
        f"Хук: {ch.hook_style.strip()}" if ch.hook_style else "",
        f"CTA: {'требуется (' + (ch.cta_style or '') + ')' if ch.cta_required is True else ('опциональный (' + (ch.cta_style or '') + ')' if ch.cta_required == 'optional' or ch.cta_required is False else '')}",
        f"Структурный совет:\n{ch.structure_hint.strip()}" if ch.structure_hint else "",
    ]
    return "\n".join(line for line in lines if line)


def _length_hint(ch: Channel) -> str:
    if ch.length.optimal_chars:
        return (
            f"цель {ch.length.optimal_chars} символов, "
            f"ОБЯЗАТЕЛЬНЫЙ ДИАПАЗОН {ch.length.min_chars or 0}–{ch.length.max_chars or '?'} "
            f"(включая footnote-блок и заголовок)"
        )
    if ch.length.duration_seconds_optimal:
        return (
            f"видео цель ~{ch.length.duration_seconds_optimal} сек, "
            f"ОБЯЗАТЕЛЬНЫЙ ДИАПАЗОН {ch.length.duration_seconds_min}–{ch.length.duration_seconds_max} сек, "
            f"≈{ch.length.words_approximate or '70-160'} слов"
        )
    return "без жёсткого лимита"


def _format_content_form(cf: ContentForm) -> str:
    parts = [cf.structure_template.strip()] if cf.structure_template else []
    if cf.hook_style:
        parts.append(f"Хук формы: {cf.hook_style}")
    if cf.requires_hero:
        parts.append("Требуется герой (но анонимизированный).")
    return "\n".join(parts)


# ─── Main entry ────────────────────────────────────────────────────────────────

def build_system_prompt(
    cfg: GenerationConfig,
    *,
    voice: VoiceProfile,
    channel: Channel,
    content_form: ContentForm,
    segment: Segment | None,
    psycho_type: PsychoType | None,
    retrieval: RetrievalContext,
    lexicon: dict,
    forbidden_topics: dict,
    raw_quotes: list[dict],
    recent_drafts: list[dict],
    few_shot_examples: list[dict] | None = None,
) -> str:
    """Собрать финальный system prompt из 5 слоёв + retrieved + diversity."""
    body = BASE_TEMPLATE.format(
        author=voice.author,
        channel_name=channel.channel_name,
        register=voice.register_,
        voice_name=voice.name,
        voice_description=(voice.description if hasattr(voice, "description") and getattr(voice, "description") else "").strip() or "(описание не указано)",
        form_of_address=voice.form_of_address,
        mat_allowed="да, точечно как усилитель" if voice.mat_allowed else "нет",
        raw_quotes_block=_format_raw_quotes(raw_quotes, voice),
        signature_phrases_block=_format_signature_phrases(retrieval, lexicon, content_form.lexicon_min),
        lexicon_min=content_form.lexicon_min,
        forbidden_block=_format_forbidden(voice, forbidden_topics),
        term_replacements=", ".join(
            f"«{k}» → " + ("УДАЛИТЬ" if v is None else f"«{v}»")
            for k, v in voice.term_replacements.items()
        ) or "нет",
        audience_block=_format_audience(segment, psycho_type, cfg.hunt_stage),
        channel_block=_format_channel(channel),
        content_form_name=content_form.name,
        content_form_block=_format_content_form(content_form),
        concepts_block=format_concepts_for_prompt(retrieval.concepts),
        segments_block=format_segments_for_prompt(retrieval.segments),
        diversity_block=format_diversity_hint(recent_drafts),
        few_shot_block=_format_few_shot_in_prompt(few_shot_examples or []),
        length_hint=_length_hint(channel),
    )

    modifier = FORM_MODIFIERS.get(content_form.slug, "")
    return body + ("\n\n" + modifier if modifier else "")


def build_user_prompt(cfg: GenerationConfig) -> str:
    """User prompt — конкретный topic_hint или общая постановка."""
    if cfg.topic_hint:
        return f"Тема для черновика: {cfg.topic_hint}"
    if cfg.topics:
        return f"Тема для черновика — что-то из топиков: {', '.join(cfg.topics)}"
    return "Подбери актуальную тему из материала выше — то, что лучше всего работает для этого канала и сегмента."


def compute_prompt_hash(system_prompt: str, user_prompt: str) -> str:
    """SHA-256 первых 16 байт собранного промта — для content_drafts.prompt_version."""
    h = hashlib.sha256((system_prompt + "\n---\n" + user_prompt).encode("utf-8"))
    return f"{PROMPT_VERSION}+{h.hexdigest()[:16]}"


def snapshot_config(
    cfg: GenerationConfig,
    voice: VoiceProfile,
    channel: Channel,
    content_form: ContentForm,
    segment: Segment | None,
    psycho_type: PsychoType | None,
) -> dict[str, Any]:
    """Snapshot всех слоёв на момент генерации → content_drafts.config_snapshot."""
    return {
        "cfg": cfg.model_dump(),
        "voice_profile": voice.model_dump(by_alias=True),
        "channel": channel.model_dump(),
        "content_form": content_form.model_dump(),
        "segment": segment.model_dump() if segment else None,
        "psycho_type": psycho_type.model_dump() if psycho_type else None,
        "prompt_version": PROMPT_VERSION,
    }
