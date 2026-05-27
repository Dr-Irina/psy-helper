"""Тесты валидаторов и трансформаций черновика."""
from __future__ import annotations

from psy_helper.content_gen import loaders
from psy_helper.content_gen.validators import (
    apply_term_replacements,
    check_forbidden_phrases,
    check_lexicon_min,
    check_provenance,
)


# ─── apply_term_replacements ──────────────────────────────────────────────────

def test_term_replacement_brak_to_supruzhestvo_exact_form():
    """Точную форму «брак» заменяет. Склоняемые формы — известное ограничение v0."""
    text = "Брак — это работа. Их брак прочен."
    out = apply_term_replacements(text, {"брак": "супружество"})
    # Обе nominative-формы заменены
    assert out.lower().count("супружество") == 2
    # А склоняемая «браке» осталась бы — это документировано в validators.py


def test_term_replacement_declined_form_not_replaced_v0_limitation():
    """Документирует ограничение: «браке» (предложный) не заменяется."""
    text = "В браке 7 лет"
    out = apply_term_replacements(text, {"брак": "супружество"})
    assert "браке" in out  # форма сохраняется (v0 limitation)


def test_term_replacement_none_removes_word():
    text = "Это истинная природа женщины"
    out = apply_term_replacements(text, {"истинная": None})
    assert "истинная" not in out.lower()
    # двойные пробелы подчищены
    assert "  " not in out


def test_term_replacement_case_insensitive():
    text = "Брак — это супружество? Брак тяжелый."
    out = apply_term_replacements(text, {"брак": "супружество"})
    assert out.lower().count("супружеств") >= 2


def test_term_replacement_respects_word_boundary():
    """Не должно превращать «бракосочетание» в «супружествосочетание»."""
    text = "бракосочетание"
    out = apply_term_replacements(text, {"брак": "супружество"})
    assert out == "бракосочетание"


# ─── check_forbidden_phrases ──────────────────────────────────────────────────

def test_forbidden_catches_antipattern_in_product_profile():
    vp = loaders.load_voice_profile("anna_product")
    forb = loaders.load_forbidden_topics()
    # Используем точно ту форму, что в антипаттернах (nominative).
    # Склоняемые формы — v0 limitation, см. validators.py.
    out = check_forbidden_phrases("Истинная природа женщины", vp, forb)
    assert any("истинная природа" in v.lower() for v in out)
    assert any(v.startswith("antipattern:") for v in out)
    assert any(v.startswith("fem_esoteric:") for v in out)


def test_forbidden_lecture_profile_allows_fem_esoteric():
    """Лекторский регистр НЕ блокирует fem_esoteric (applies_to=[product,joint_product])."""
    vp = loaders.load_voice_profile("anna_lecture")
    forb = loaders.load_forbidden_topics()
    # лекторский не имеет «истинная природа» в antipatterns тоже
    out = check_forbidden_phrases("Истинная природа", vp, forb)
    assert not any("fem_esoteric" in v for v in out)


def test_forbidden_catches_sect_tone_in_all_profiles():
    """sect_tone — applies_to=[all]. Срабатывает везде."""
    forb = loaders.load_forbidden_topics()
    for slug in ("anna_lecture", "anna_product", "joint_product"):
        vp = loaders.load_voice_profile(slug)
        out = check_forbidden_phrases("Наш круг знает", vp, forb)
        assert any("sect_tone" in v for v in out), f"{slug} не поймал sect_tone"


def test_forbidden_clean_text_returns_empty():
    vp = loaders.load_voice_profile("anna_product")
    forb = loaders.load_forbidden_topics()
    out = check_forbidden_phrases(
        "Если Вы устали в супружестве — это нормально.",
        vp, forb,
    )
    assert out == []


# ─── check_provenance ─────────────────────────────────────────────────────────

def test_provenance_all_valid():
    text = "Тезис один [^c123]. Тезис два [^s456]."
    bad = check_provenance(text, available_concept_ids=[123], available_segment_ids=[456])
    assert bad == []


def test_provenance_detects_invalid_concept_id():
    text = "Тезис [^c999]."
    bad = check_provenance(text, available_concept_ids=[123], available_segment_ids=[])
    assert bad == ["c999"]


def test_provenance_detects_invalid_segment_id():
    text = "Тезис [^s999]."
    bad = check_provenance(text, available_concept_ids=[], available_segment_ids=[456])
    assert bad == ["s999"]


def test_provenance_no_footnotes_returns_empty():
    bad = check_provenance("Просто текст", [], [])
    assert bad == []


# ─── check_lexicon_min ────────────────────────────────────────────────────────

def test_lexicon_min_counts_signature_phrases():
    lex = loaders.load_lexicon()
    text = "Так себя не ведут, говорит мама. Чей это голос?"
    count = check_lexicon_min(text, lex, required=2)
    assert count >= 2


def test_lexicon_min_zero_returns_zero_fast():
    """При required=0 не делаем работы."""
    lex = loaders.load_lexicon()
    assert check_lexicon_min("любой текст", lex, required=0) == 0
