-- Универсальный store для черновиков контента (TG/Insta/email/звонок/тикток/...).
--
-- Заполняется через psy_helper/content_gen/generator.py: одна строка = один draft
-- от LLM, со всем контекстом для reproducibility и аудита.
--
-- Lifecycle:  draft → approved | rejected | failed → published
--   - draft     : только что сгенерён, не ревьюили
--   - approved  : Аня (или ревьюер) одобрила, годен для публикации / few-shot loop
--   - rejected  : не подошёл, причина в review_notes
--   - failed    : ошибка генерации (rate_limit / timeout / context_too_long) — см. failure_reason
--   - published : опубликовано во внешнем канале (для Phase 3+ интеграций)
--
-- Multi-tenant ready: therapist_id обязателен, никакого хардкода Анны.
--
-- Версионирование промта: prompt_version + config_snapshot позволяют:
--   - воспроизвести любой draft через год
--   - делать A/B между версиями шаблона
--   - анализировать какие конфиги дают approved-rate выше

CREATE TABLE IF NOT EXISTS content_drafts (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    therapist_id             UUID NOT NULL REFERENCES therapists(id),

    -- Layered config (5 слоёв)
    voice_profile_slug       TEXT NOT NULL,
    channel_slug             TEXT NOT NULL,
    content_form_slug        TEXT NOT NULL,
    segment_slug             TEXT,
    psycho_type_slug         TEXT,

    -- Параметры таргетинга
    hunt_stage               INTEGER,
    topics                   TEXT[],
    topic_hint               TEXT,

    -- Сам контент
    content                  TEXT NOT NULL,
    provenance               JSONB,           -- { "c123": concept_id, "s456": segment_id, ... }

    -- Версионирование и reproducibility
    prompt_version           TEXT NOT NULL,   -- "v0.1.0" / sha-хеш собранного промта
    config_snapshot          JSONB NOT NULL,  -- snapshot всех 5 layers + параметров на момент генерации

    -- Cost / model tracking
    model                    TEXT NOT NULL,   -- "claude-sonnet-4-6" | "claude-haiku-4-5"
    cost_usd                 NUMERIC(10, 4),
    tokens_input             INT,
    tokens_output            INT,
    cache_creation_tokens    INT,
    cache_read_tokens        INT,

    -- PII / safety
    pii_flags                TEXT[],          -- ["suspicious_name:Маша", "phone:+7..."] для review

    -- Lifecycle
    status                   TEXT NOT NULL DEFAULT 'draft'
                             CHECK (status IN ('draft','approved','rejected','failed','published')),
    reviewed_by              TEXT,
    review_notes             TEXT,
    failure_reason           TEXT,

    -- Timestamps + perf
    created_at               TIMESTAMP NOT NULL DEFAULT NOW(),
    reviewed_at              TIMESTAMP,
    published_at             TIMESTAMP,
    generation_duration_ms   INT
);

-- Базовые фильтры для UI «📋 Черновики»
CREATE INDEX IF NOT EXISTS idx_content_drafts_therapist_status
    ON content_drafts (therapist_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_content_drafts_filter
    ON content_drafts (voice_profile_slug, channel_slug, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_content_drafts_segment_stage
    ON content_drafts (segment_slug, hunt_stage);

-- Diversity-penalty: быстро находить past drafts по (channel, segment) для anti-repeat
CREATE INDEX IF NOT EXISTS idx_content_drafts_diversity
    ON content_drafts (channel_slug, segment_slug, created_at DESC);

-- GIN на topics для фильтра «все драфты про supruzhestvo за месяц»
CREATE INDEX IF NOT EXISTS idx_content_drafts_topics
    ON content_drafts USING GIN (topics);
