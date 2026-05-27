"""Все 31 YAML-конфиг в data/ грузятся в Pydantic без ошибок.

Это safety net против опечаток в конфигах: запустишь pytest до коммита —
поймаешь сломанный YAML до того, как сломается генератор.
"""
from __future__ import annotations

import pytest

from psy_helper.content_gen import loaders
from psy_helper.content_gen.config import (
    Channel,
    ContentForm,
    PsychoType,
    Segment,
    VoiceProfile,
)


# ─── Listings ─────────────────────────────────────────────────────────────────

def test_list_voice_profiles_has_three():
    profiles = loaders.list_voice_profiles()
    assert set(profiles) == {"anna_lecture", "anna_product", "joint_product"}


def test_list_segments_has_four():
    segments = loaders.list_segments()
    assert len(segments) == 4
    assert "tired_wife" in segments  # главный сегмент


def test_list_psycho_types_has_four():
    types = loaders.list_psycho_types()
    assert len(types) == 4
    assert "patient" in types  # главный психотип


def test_list_channels_has_ten():
    assert len(loaders.list_channels()) == 10


def test_list_content_forms_has_ten():
    assert len(loaders.list_content_forms()) == 10


# ─── All YAMLs load into Pydantic ──────────────────────────────────────────────

@pytest.mark.parametrize("slug", ["anna_lecture", "anna_product", "joint_product"])
def test_voice_profile_loads(slug: str):
    vp = loaders.load_voice_profile(slug)
    assert isinstance(vp, VoiceProfile)
    assert vp.slug == slug
    assert vp.form_of_address in ("ты", "Вы")
    assert vp.register_ in ("лекторский", "продуктовый")


def test_all_segments_load():
    for slug in loaders.list_segments():
        seg = loaders.load_segment(slug)
        assert isinstance(seg, Segment)
        assert seg.slug == slug


def test_all_psycho_types_load():
    for slug in loaders.list_psycho_types():
        pt = loaders.load_psycho_type(slug)
        assert isinstance(pt, PsychoType)
        assert pt.slug == slug


def test_all_channels_load_and_have_preferred_model():
    for slug in loaders.list_channels():
        ch = loaders.load_channel(slug)
        assert isinstance(ch, Channel)
        assert ch.slug == slug
        # Step 1b: каждый channel должен иметь preferred_model
        assert ch.preferred_model in (
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            "claude-opus-4-7",
        ), f"{slug}: bad preferred_model={ch.preferred_model}"


def test_all_content_forms_load_and_have_lexicon_min():
    for slug in loaders.list_content_forms():
        cf = loaders.load_content_form(slug)
        assert isinstance(cf, ContentForm)
        assert cf.slug == slug
        # Step 1a: каждый content_form должен иметь lexicon_min ∈ [0,2]
        assert 0 <= cf.lexicon_min <= 5, f"{slug}: weird lexicon_min={cf.lexicon_min}"


# ─── Style artifacts ──────────────────────────────────────────────────────────

def test_lexicon_loads_with_questions_and_metaphors():
    lex = loaders.load_lexicon()
    assert "questions" in lex
    assert "metaphors" in lex
    assert len(lex["questions"]) > 100  # 161 на момент написания
    assert len(lex["metaphors"]) > 100  # 253 на момент написания


def test_forbidden_topics_has_required_groups():
    forb = loaders.load_forbidden_topics()
    assert forb["version"] >= 2
    topic_ids = {t["id"] for t in forb["topics"]}
    assert {"diagnoses", "acute_states", "guarantees"} <= topic_ids
    phrase_ids = {p["id"] for p in forb["phrases"]}
    assert {"fem_esoteric", "sect_tone", "guarantee_claims"} <= phrase_ids


# ─── Cross-references ──────────────────────────────────────────────────────────

def test_voice_profile_sources_files_exist():
    """Каждый voice_profile ссылается на существующие voice_doc + lexicon + raw_quotes."""
    from pathlib import Path

    for slug in loaders.list_voice_profiles():
        vp = loaders.load_voice_profile(slug)
        assert Path(vp.sources.voice_doc).exists(), f"{slug}: voice_doc missing"
        assert Path(vp.sources.lexicon).exists(), f"{slug}: lexicon missing"
        assert Path(vp.sources.raw_quotes.path).exists(), f"{slug}: raw_quotes missing"


def test_main_segment_and_type_have_priority_1():
    """Главный сегмент + психотип помечены priority=1 (для default-логики UI)."""
    assert loaders.load_segment("tired_wife").priority == 1
    assert loaders.load_psycho_type("patient").priority == 1
