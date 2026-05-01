-- ТЗ §6 (полная схема) + дополнения §18 (multi-therapist ready, HNSW вместо ivfflat).
-- Применяется один раз при инициализации БД.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- Multi-therapist ready: §18.4
CREATE TABLE IF NOT EXISTS therapists (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Сырьё транскрипций (ТЗ §1b: therapy_status, eligible_for_processing_at)
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_transcripts (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  therapist_id                UUID REFERENCES therapists(id),
  source_type                 TEXT NOT NULL,                    -- 'lecture' | 'session' | 'bot_voice'
  source_file                 TEXT NOT NULL,
  source_hash                 TEXT,                              -- sha256 для идемпотентности
  recorded_at                 TIMESTAMP,
  content                     JSONB NOT NULL,                   -- raw.json от WhisperX
  metadata                    JSONB,                            -- metadata.json (модель, диаризация, ...)
  client_id                   UUID,
  -- §1b: правила работы с записями разработчика-клиента
  therapy_status              TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'closed' | 'excluded'
  eligible_for_processing_at  TIMESTAMP,
  closed_by_therapist_at      TIMESTAMP,
  ingested_at                 TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE (source_file, source_hash)
);

CREATE INDEX IF NOT EXISTS idx_raw_transcripts_source ON raw_transcripts (source_type, therapy_status);
CREATE INDEX IF NOT EXISTS idx_raw_transcripts_client ON raw_transcripts (client_id);

-- ============================================================
-- Очищенные сегменты (после смысловой нарезки, ТЗ §4.2)
-- ============================================================
CREATE TABLE IF NOT EXISTS clean_segments (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  raw_id        UUID NOT NULL REFERENCES raw_transcripts ON DELETE CASCADE,
  start_ts      FLOAT,
  end_ts        FLOAT,
  title         TEXT,
  summary       TEXT,
  text          TEXT NOT NULL,
  segment_type  TEXT,                        -- 'lecture_block' | 'therapy_episode' | ...
  metadata      JSONB,
  created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clean_segments_raw ON clean_segments (raw_id);

CREATE TABLE IF NOT EXISTS segment_embeddings (
  segment_id  UUID PRIMARY KEY REFERENCES clean_segments ON DELETE CASCADE,
  embedding   vector(1024)                   -- multilingual-e5/bge-m3 = 1024-dim (см. §18.3 — итоговая размерность определится при выборе модели)
);

CREATE INDEX IF NOT EXISTS idx_segment_embeddings_hnsw
  ON segment_embeddings USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- Метод: концепты и стиль (multi-therapist, §18.4)
-- ============================================================
CREATE TABLE IF NOT EXISTS concepts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  therapist_id    UUID NOT NULL REFERENCES therapists(id),
  name            TEXT NOT NULL,
  type            TEXT,                       -- см. psy_helper/taxonomy.py:
                                              --   term, technique, example, claim,
                                              --   recommendation, exercise, warning,
                                              --   question, metaphor
  description     TEXT,
  source_segments UUID[],
  embedding       vector(1024),
  created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE (therapist_id, name)
);

CREATE INDEX IF NOT EXISTS idx_concepts_hnsw
  ON concepts USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS therapist_moves (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  therapist_id    UUID NOT NULL REFERENCES therapists(id),
  move_type       TEXT,
  trigger_context TEXT,
  phrasing        TEXT,
  tags            TEXT[],
  embedding       vector(1024),
  created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_therapist_moves_hnsw
  ON therapist_moves USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- Voice-document (версионированный, ТЗ §6)
-- ============================================================
CREATE TABLE IF NOT EXISTS voice_document (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  therapist_id     UUID NOT NULL REFERENCES therapists(id),
  version          INT NOT NULL,
  content          TEXT NOT NULL,
  changes_summary  TEXT,
  created_by       UUID,
  created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
  is_active        BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (therapist_id, version)
);

-- ============================================================
-- Граф клиента (ТЗ §6, HNSW по §18.3)
-- ============================================================
CREATE TABLE IF NOT EXISTS nodes (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id                UUID NOT NULL,
  type                     TEXT NOT NULL,
  name                     TEXT,
  properties               JSONB,
  version                  INT NOT NULL DEFAULT 1,
  validated_by_therapist   BOOLEAN NOT NULL DEFAULT FALSE,
  created_at               TIMESTAMP NOT NULL DEFAULT NOW(),
  session_id               UUID,
  embedding                vector(1024)
);

CREATE INDEX IF NOT EXISTS idx_nodes_client_type ON nodes (client_id, type);
CREATE INDEX IF NOT EXISTS idx_nodes_hnsw
  ON nodes USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS edges (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  from_node   UUID NOT NULL REFERENCES nodes ON DELETE CASCADE,
  to_node     UUID NOT NULL REFERENCES nodes ON DELETE CASCADE,
  type        TEXT NOT NULL,
  weight      FLOAT DEFAULT 1.0,
  properties  JSONB,
  created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_edges_from ON edges (from_node);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges (to_node);

-- ============================================================
-- Диалоги и сообщения (ТЗ §6)
-- ============================================================
CREATE TABLE IF NOT EXISTS conversations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id   UUID NOT NULL,
  started_at  TIMESTAMP NOT NULL DEFAULT NOW(),
  ended_at    TIMESTAMP,
  mode        TEXT NOT NULL,                 -- 'open' | 'diary' | 'homework' | 'prep' | 'reflection' | 'listen_only'
  metadata    JSONB
);

CREATE TABLE IF NOT EXISTS messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations ON DELETE CASCADE,
  role            TEXT NOT NULL,
  content         TEXT,
  audio_path      TEXT,
  voice_features  JSONB,
  metadata        JSONB,
  created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id);

CREATE TABLE IF NOT EXISTS therapist_flags (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id  UUID NOT NULL REFERENCES messages ON DELETE CASCADE,
  flag_type   TEXT NOT NULL,
  note        TEXT,
  created_by  TEXT,                          -- 'client' | 'therapist' | 'system'
  created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Голос (ТЗ §5.7)
-- ============================================================
CREATE TABLE IF NOT EXISTS voice_baselines (
  client_id     UUID PRIMARY KEY,
  features      JSONB NOT NULL,
  samples_count INT NOT NULL DEFAULT 0,
  updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS voice_anomalies (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id              UUID NOT NULL,
  message_id             UUID REFERENCES messages ON DELETE CASCADE,
  severity               TEXT NOT NULL,      -- 'moderate' | 'strong'
  features_z             JSONB,
  reviewed_by_therapist  BOOLEAN NOT NULL DEFAULT FALSE,
  created_at             TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Мета-отчёты (ТЗ §6, multi-therapist §18.4)
-- ============================================================
CREATE TABLE IF NOT EXISTS system_reports (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  therapist_id  UUID NOT NULL REFERENCES therapists(id),
  period_start  DATE NOT NULL,
  period_end    DATE NOT NULL,
  content       JSONB NOT NULL,
  created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
