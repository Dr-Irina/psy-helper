-- Фирменные фразы Анны (вопросы + метафоры) как поисковый источник.
--
-- ИСТОЧНИК ИСТИНЫ: data/style/lexicon.json (там Анна с пользователем правит).
-- БД — это «индексированная копия» с embeddings для семантического поиска.
-- Скрипт scripts/ingest_lexicon.py перезаливает таблицу из JSON
-- (TRUNCATE + INSERT, идемпотентно, без потерь — JSON остаётся источником).
--
-- kind: 'question' или 'metaphor' (так разделено в lexicon.json).
-- phrase  — фирменная фраза (короткая, она же id).
-- description — описание/контекст (на этом тоже строим BM25/embedding).
-- mentions — сколько раз встречалось в корпусе.

CREATE TABLE IF NOT EXISTS lexicon_items (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind        TEXT NOT NULL CHECK (kind IN ('question', 'metaphor')),
    phrase      TEXT NOT NULL,
    description TEXT,
    mentions    INT,
    embedding   vector(1024),
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (kind, phrase)
);

CREATE INDEX IF NOT EXISTS idx_lexicon_hnsw
    ON lexicon_items USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_lexicon_search
    ON lexicon_items USING GIN (
        to_tsvector('russian',
            COALESCE(phrase, '') || ' ' || COALESCE(description, ''))
    );

CREATE INDEX IF NOT EXISTS idx_lexicon_kind
    ON lexicon_items (kind);
