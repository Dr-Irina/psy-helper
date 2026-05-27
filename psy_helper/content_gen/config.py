"""Pydantic-модели для 5 слоёв конфига контент-генератора + GenerationConfig.

Принципы:
    - модели тонкие — описаны только поля, нужные runtime'у генератора.
    - `extra="allow"` — описательные поля YAML (descriptions, audience_research_note, etc.)
      не ломают загрузку, но не валидируются.
    - валидация slug'ов отложена до loaders.py — здесь только типы.
    - все списки имеют дефолты — отсутствие поля в YAML не падает.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ─── Layer 1: Voice profile ────────────────────────────────────────────────────

class RawQuotesFilter(BaseModel):
    model_config = ConfigDict(extra="allow")
    remove_mat: bool = False
    max_quotes: int = 10


class RawQuotesSource(BaseModel):
    model_config = ConfigDict(extra="allow")
    path: str
    filter: RawQuotesFilter = Field(default_factory=RawQuotesFilter)


class VoiceProfileSources(BaseModel):
    model_config = ConfigDict(extra="allow")
    voice_doc: str
    lexicon: str
    raw_quotes: RawQuotesSource


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    slug: str
    name: str
    author: str
    form_of_address: Literal["ты", "Вы"]
    # YAML key — "register"; в коде — register_ (BaseModel.register shadowed warning)
    register_: Literal["лекторский", "продуктовый"] = Field(alias="register")

    mat_allowed: bool = False
    provocation_allowed: Literal["low", "medium", "high"] = "low"

    sources: VoiceProfileSources
    antipatterns: list[str] = Field(default_factory=list)
    term_replacements: dict[str, str | None] = Field(default_factory=dict)

    placeholder: bool = False
    pending_oksana_corpus: bool = False
    joint_markers: list[str] = Field(default_factory=list)


# ─── Layer 2: Segment ──────────────────────────────────────────────────────────

class Segment(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str
    name: str
    priority: int = 99

    situation: str = ""
    pain_phrases: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    main_message: str = ""

    main_psycho_types: list[str] = Field(default_factory=list)
    primary_psycho_type: str | None = None

    active_hours: dict[str, str] = Field(default_factory=dict)


# ─── Layer 3: Psycho type ──────────────────────────────────────────────────────

class PsychoType(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str
    name: str
    priority: int = 99

    motivator: str = ""
    decision_speed: str = ""

    attracts: list[str] = Field(default_factory=list)
    repels: list[str] = Field(default_factory=list)
    best_formats: list[str] = Field(default_factory=list)
    cta_examples: list[str] = Field(default_factory=list)
    key_argument: str = ""


# ─── Layer 4: Channel ──────────────────────────────────────────────────────────

class ChannelLength(BaseModel):
    """Полиморфная длина: chars для текста, duration/words для видео."""
    model_config = ConfigDict(extra="allow")

    min_chars: int | None = None
    optimal_chars: int | None = None
    max_chars: int | None = None
    duration_seconds_min: int | None = None
    duration_seconds_optimal: int | None = None
    duration_seconds_max: int | None = None
    words_approximate: str | None = None


class Channel(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str
    name: str
    format: str
    channel_name: str

    length: ChannelLength
    hook_style: str = ""
    cta_required: bool | str = False  # bool ИЛИ "optional"
    cta_style: str = ""
    voice_form_default: Literal["ты", "Вы"] = "Вы"
    structure_hint: str = ""

    best_psycho_types: list[str] = Field(default_factory=list)
    best_segments: list[str] = Field(default_factory=list)
    best_hunt_stages: list[int] = Field(default_factory=list)
    best_content_forms: list[str] = Field(default_factory=list)

    preferred_model: str = "claude-sonnet-4-6"
    requires_map_reduce: bool = False


# ─── Layer 5: Content form ─────────────────────────────────────────────────────

class ContentForm(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str
    name: str

    structure_template: str = ""
    hook_style: str = ""
    requires_hero: bool = False
    example_outline: str = ""

    best_channels: list[str] = Field(default_factory=list)
    best_psycho_types: list[str] = Field(default_factory=list)
    best_hunt_stages: list[int] = Field(default_factory=list)

    lexicon_min: int = 2
    notes: list[str] = Field(default_factory=list)


# ─── Generation config — input для generator.py ────────────────────────────────

class GenerationConfig(BaseModel):
    """Один request к генератору. Все 5 слоёв + параметры таргетинга."""
    model_config = ConfigDict(extra="forbid")

    therapist_slug: str = "anna"  # для multi-tenant в будущем

    # 5 layers (slug'и; объекты резолвятся в loaders при сборке промта)
    voice_profile: str
    channel: str
    content_form: str
    segment: str | None = None
    psycho_type: str | None = None

    # Параметры таргетинга
    hunt_stage: int | None = None
    topics: list[str] = Field(default_factory=list)
    topic_hint: str | None = None

    # Override defaults
    model_override: str | None = None
    inline_overrides: dict[str, Any] = Field(default_factory=dict)


# ─── Output draft ──────────────────────────────────────────────────────────────

class DraftCost(BaseModel):
    cost_usd: float
    tokens_input: int
    tokens_output: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


class ContentDraft(BaseModel):
    """То, что generator возвращает (и кладёт в content_drafts)."""
    model_config = ConfigDict(extra="forbid")

    id: str | None = None  # заполняется при save
    content: str
    provenance: dict[str, str] = Field(default_factory=dict)
    pii_flags: list[str] = Field(default_factory=list)
    prompt_version: str
    config_snapshot: dict[str, Any]
    model: str
    cost: DraftCost
    generation_duration_ms: int
