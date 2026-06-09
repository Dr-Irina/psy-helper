-- 007: голос Ани + значимость концептов + спикер сегментов.
--
-- Перевыделение концептов (см. план glittery-sauteeing-wreath.md). Проблема старого
-- корпуса: description — аналитический пересказ, голос Ани теряется; нет веса/значимости.
-- Новая структура концепта:
--   name        — короткое имя (как раньше)
--   description — аналитично, для ПОИСКА (как раньше, эмбеддится)
--   quotes      — массив ДОСЛОВНЫХ цитат Ани (по одной на упоминание) → ГОЛОС.
--                 Накапливается при консолидации дублей по 68 лекциям.
--                 Формат: [{ "text": "...", "segment_id": "<uuid|null>", "speaker": "SPEAKER_X" }]
--   salience    — значимость 1-3 (3 = центральный тезис лекции, 1 = проходной) → ВЕС
--
-- speaker на clean_segments — доминирующий по диаризации спикер блока (для фильтрации
-- «только реплики Ани» и аудита). См. scripts/analyze_speakers.py + data/speakers.json.

ALTER TABLE concepts ADD COLUMN IF NOT EXISTS quotes   JSONB;
ALTER TABLE concepts ADD COLUMN IF NOT EXISTS salience SMALLINT;

ALTER TABLE clean_segments ADD COLUMN IF NOT EXISTS speaker TEXT;

-- Приоритет по значимости в ретривале/генерации (concepts ORDER BY salience DESC).
CREATE INDEX IF NOT EXISTS idx_concepts_salience ON concepts (salience DESC NULLS LAST);
