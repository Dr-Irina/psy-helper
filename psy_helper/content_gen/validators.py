"""Пост-генерация валидаторы и трансформации черновика.

Применяются в порядке:
    1. apply_term_replacements   — «брак» → «супружество» и т.п.
    2. check_forbidden_phrases   — список запрещённых формулировок
    3. check_provenance          — все [^c123]/[^s456] ссылки ведут на реальные id
    4. check_lexicon_min         — минимум N фирменных фраз
    5. detect_pii (отдельный модуль)

Все check_* возвращают list[str] нарушений (пустой = ОК).
Все apply_* возвращают новый текст.

ИЗВЕСТНОЕ ОГРАНИЧЕНИЕ v0 (Russian morphology):
    Substring/word-boundary матчинг ловит только точную форму слова.
    «брак» в антипаттернах НЕ сматчит «браке», «истинная природа» НЕ сматчит
    «истинную природу». Лечится либо расширением списков склоняемыми формами,
    либо стеммером (pymorphy2). На v0 кладёмся на то, что LLM получает явную
    инструкцию использовать «супружество» / избегать антипаттернов — и в большинстве
    случаев пишет нужные формы изначально.
"""
from __future__ import annotations

import re
from typing import Iterable

from psy_helper.content_gen.config import VoiceProfile


_PROVENANCE_RE = re.compile(r"\[\^([cs])(\d+)\]")


# ─── Transformations ──────────────────────────────────────────────────────────

def apply_term_replacements(text: str, replacements: dict[str, str | None]) -> str:
    """Замены из voice_profile.term_replacements.

    None в значении = удалить термин (заменить на пустую строку с подчисткой пробелов).
    Case-insensitive поиск, сохраняем кейс первой буквы в замене где уместно.
    """
    out = text
    for src, dst in replacements.items():
        if dst is None:
            # Удалить с подчисткой двойных пробелов
            out = re.sub(rf"\b{re.escape(src)}\b\s*", "", out, flags=re.IGNORECASE)
            out = re.sub(r"  +", " ", out)
        else:
            out = re.sub(rf"\b{re.escape(src)}\b", dst, out, flags=re.IGNORECASE)
    return out


# ─── Checks ───────────────────────────────────────────────────────────────────

def check_forbidden_phrases(
    text: str,
    voice_profile: VoiceProfile,
    forbidden_topics: dict,
) -> list[str]:
    """Найти запрещённые фразы из:
        - voice_profile.antipatterns (per-profile)
        - forbidden_topics['phrases'] где applies_to включает register
    Возвращает список вида ["antipattern:женственность", "fem_esoteric:наш круг"].
    """
    text_lower = text.lower()
    violations: list[str] = []

    for phrase in voice_profile.antipatterns:
        if phrase.lower() in text_lower:
            violations.append(f"antipattern:{phrase}")

    register_keys = {
        "лекторский": {"all", "lecturer"},
        "продуктовый": {"all", "product"},
    }.get(voice_profile.register_, {"all"})

    if voice_profile.slug == "joint_product":
        register_keys = register_keys | {"joint_product"}

    for group in forbidden_topics.get("phrases", []):
        if not (set(group.get("applies_to", [])) & register_keys):
            continue
        for phrase in group.get("phrases", []):
            if phrase.lower() in text_lower:
                violations.append(f"{group['id']}:{phrase}")

    return violations


def check_provenance(
    text: str,
    available_concept_ids: Iterable[int],
    available_segment_ids: Iterable[int],
) -> list[str]:
    """Все [^cN] и [^sN] в тексте должны ссылаться на id из retrieved-выборки.

    Возвращает список несуществующих ссылок ["c999", "s42"].
    Пустой [] = всё ок.
    """
    concept_ids = {str(i) for i in available_concept_ids}
    segment_ids = {str(i) for i in available_segment_ids}
    bad: list[str] = []
    for m in _PROVENANCE_RE.finditer(text):
        kind, num = m.group(1), m.group(2)
        pool = concept_ids if kind == "c" else segment_ids
        if num not in pool:
            bad.append(f"{kind}{num}")
    return bad


def check_length(text: str, channel) -> str | None:
    """Вернёт описание нарушения если длина вне диапазона канала, иначе None.

    Для текстовых каналов сверяемся с *_chars. Для видео-каналов с
    duration_seconds_max (приближённо через слова: 1 слово ≈ 0.4 сек).
    """
    n = len(text)
    L = channel.length

    if L.max_chars is not None and n > L.max_chars:
        return f"length_over_max:{n}/{L.max_chars}"
    if L.min_chars is not None and n < L.min_chars:
        return f"length_under_min:{n}/{L.min_chars}"

    # Видео: грубая оценка через слова
    if L.duration_seconds_max is not None:
        words = len(text.split())
        approx_sec = words / 2.5  # ~150 слов/мин = 2.5 слова/сек
        if approx_sec > L.duration_seconds_max:
            return f"length_over_max_sec:{int(approx_sec)}/{L.duration_seconds_max}"

    return None


def estimate_max_output_tokens(channel, *, char_per_token: float = 2.0, buffer_ratio: float = 1.15) -> int:
    """Рассчитать max_tokens для Anthropic API из channel.length.max_chars.

    Для русского text ≈ 2 chars/token. Buffer 15% — на footnote-блок и форматирование.
    Минимум 200 (для quote_card / email_subject), максимум 8000 (для длинных).
    """
    L = channel.length
    if L.max_chars is not None:
        tokens = int((L.max_chars / char_per_token) * buffer_ratio)
        return max(200, min(8000, tokens))
    if L.duration_seconds_max is not None:
        # ~2.5 слова/сек × ~1.5 token/слово
        tokens = int(L.duration_seconds_max * 2.5 * 1.5 * buffer_ratio)
        return max(200, min(8000, tokens))
    return 2000  # дефолт если канал ничего не указал


def check_lexicon_min(text: str, lexicon: dict, required: int) -> int:
    """Сколько фирменных фраз (questions + metaphors) встречается в тексте.

    Возвращает count. Сравнение с required — на стороне вызывающего.
    """
    if required <= 0:
        return 0
    phrases: list[str] = []
    phrases.extend(item["phrase"] for item in lexicon.get("questions", []))
    phrases.extend(item["phrase"] for item in lexicon.get("metaphors", []))

    text_lower = text.lower()
    return sum(1 for p in phrases if p.lower() in text_lower)
