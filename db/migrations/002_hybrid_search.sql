-- Гибридный поиск: tsvector колонки + GIN-индексы для BM25.
-- Веса: A (наивысший) — название/заголовок, B — описание/резюме, C — текст.
-- Используется русский словарь Postgres (стемминг, стоп-слова).

ALTER TABLE concepts
  ADD COLUMN IF NOT EXISTS search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('russian', COALESCE(name, '')), 'A') ||
    setweight(to_tsvector('russian', COALESCE(description, '')), 'B')
  ) STORED;

CREATE INDEX IF NOT EXISTS idx_concepts_search
  ON concepts USING GIN(search_tsv);

ALTER TABLE clean_segments
  ADD COLUMN IF NOT EXISTS search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('russian', COALESCE(title, '')), 'A') ||
    setweight(to_tsvector('russian', COALESCE(summary, '')), 'B') ||
    setweight(to_tsvector('russian', COALESCE(text, '')), 'C')
  ) STORED;

CREATE INDEX IF NOT EXISTS idx_clean_segments_search
  ON clean_segments USING GIN(search_tsv);
