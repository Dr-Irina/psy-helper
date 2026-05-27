"""Загрузка 5 слоёв конфига из YAML/JSON в Pydantic-объекты.

LRU-кэш на process lifetime — конфиги читаются один раз и переиспользуются.
Для hot-reload в Streamlit вызывается `clear_cache()`.

Все пути относительны к корню репо (DATA_ROOT).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml

from psy_helper.content_gen.config import (
    Channel,
    ContentForm,
    PsychoType,
    Segment,
    VoiceProfile,
)

DATA_ROOT = Path("data")

VOICE_PROFILES_DIR = DATA_ROOT / "voice_profiles"
SEGMENTS_DIR = DATA_ROOT / "audience" / "segments"
PSYCHO_TYPES_DIR = DATA_ROOT / "audience" / "psycho_types"
CHANNELS_DIR = DATA_ROOT / "channels"
CONTENT_FORMS_DIR = DATA_ROOT / "content_forms"
STYLE_DIR = DATA_ROOT / "style"
VOICE_DOCS_DIR = DATA_ROOT / "voice_document"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _find_by_slug(directory: Path, slug: str) -> Path:
    """Файлы могут быть `{slug}.yaml` или `{N}_{slug}.yaml` (для priority)."""
    direct = directory / f"{slug}.yaml"
    if direct.exists():
        return direct
    matches = list(directory.glob(f"*_{slug}.yaml"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"Несколько файлов matches {slug} в {directory}: {matches}")
    raise FileNotFoundError(f"Не найден config для slug={slug!r} в {directory}")


# ─── Loaders ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=32)
def load_voice_profile(slug: str) -> VoiceProfile:
    return VoiceProfile.model_validate(_read_yaml(_find_by_slug(VOICE_PROFILES_DIR, slug)))


@lru_cache(maxsize=32)
def load_segment(slug: str) -> Segment:
    return Segment.model_validate(_read_yaml(_find_by_slug(SEGMENTS_DIR, slug)))


@lru_cache(maxsize=32)
def load_psycho_type(slug: str) -> PsychoType:
    return PsychoType.model_validate(_read_yaml(_find_by_slug(PSYCHO_TYPES_DIR, slug)))


@lru_cache(maxsize=64)
def load_channel(slug: str) -> Channel:
    return Channel.model_validate(_read_yaml(_find_by_slug(CHANNELS_DIR, slug)))


@lru_cache(maxsize=64)
def load_content_form(slug: str) -> ContentForm:
    return ContentForm.model_validate(_read_yaml(_find_by_slug(CONTENT_FORMS_DIR, slug)))


# ─── Style artifacts ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_lexicon() -> dict:
    return _read_json(STYLE_DIR / "lexicon.json")


@lru_cache(maxsize=1)
def load_forbidden_topics() -> dict:
    return _read_json(STYLE_DIR / "forbidden_topics.json")


@lru_cache(maxsize=4)
def load_raw_quotes(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"raw_quotes not found: {p}")
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


@lru_cache(maxsize=4)
def load_voice_doc(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"voice_doc not found: {p}")
    return p.read_text(encoding="utf-8")


# ─── Listing (для UI dropdown'ов) ──────────────────────────────────────────────

def list_voice_profiles() -> list[str]:
    return sorted(p.stem for p in VOICE_PROFILES_DIR.glob("*.yaml"))


def list_segments() -> list[str]:
    return sorted(
        yaml.safe_load(p.read_text(encoding="utf-8"))["slug"]
        for p in SEGMENTS_DIR.glob("*.yaml")
    )


def list_psycho_types() -> list[str]:
    return sorted(
        yaml.safe_load(p.read_text(encoding="utf-8"))["slug"]
        for p in PSYCHO_TYPES_DIR.glob("*.yaml")
    )


def list_channels() -> list[str]:
    return sorted(p.stem for p in CHANNELS_DIR.glob("*.yaml"))


def list_content_forms() -> list[str]:
    return sorted(p.stem for p in CONTENT_FORMS_DIR.glob("*.yaml"))


def clear_cache() -> None:
    """Drop all LRU caches — для hot-reload в dev / UI."""
    for fn in (
        load_voice_profile, load_segment, load_psycho_type,
        load_channel, load_content_form,
        load_lexicon, load_forbidden_topics, load_raw_quotes, load_voice_doc,
    ):
        fn.cache_clear()
