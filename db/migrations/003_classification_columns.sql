-- Multi-label классификация концептов для контент-воронки.
--
-- topics       — фиксированный список из 9 значений (см. ТЗ воронки §2):
--                marriage, partnership, children, teens, confidence,
--                personal_effectiveness, finance, communication, general.
-- subtopics    — open-ended LLM-генерируемые теги (русские, канонизируются при ≥5 повторов).
-- hunt_stages  — массив подходящих ступеней лестницы Ханта (1-5).
--
-- Заполняются батчем через Anthropic Haiku 4.5 — см. scripts/classify_concepts_full.py.

ALTER TABLE concepts ADD COLUMN IF NOT EXISTS topics      TEXT[];
ALTER TABLE concepts ADD COLUMN IF NOT EXISTS subtopics   TEXT[];
ALTER TABLE concepts ADD COLUMN IF NOT EXISTS hunt_stages INTEGER[];

-- GIN-индексы для array containment-запросов (concepts WHERE topics @> ARRAY['marriage'])
CREATE INDEX IF NOT EXISTS idx_concepts_topics      ON concepts USING GIN (topics);
CREATE INDEX IF NOT EXISTS idx_concepts_subtopics   ON concepts USING GIN (subtopics);
CREATE INDEX IF NOT EXISTS idx_concepts_hunt_stages ON concepts USING GIN (hunt_stages);
