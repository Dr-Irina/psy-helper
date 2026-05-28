-- Заметки/правки на исходные документы генератора.
--
-- ЦЕЛЬ: дать Анне обратную связь на voice_doc, lexicon, антипаттерны,
-- сегменты, психотипы, каналы и формы — не только на готовые драфты.
-- Накопленные annotation'ы потом используются при regen'е v_next:
-- v3 voice_doc / v2 lexicon / расширенные forbidden_phrases.
--
-- source_type / source_id:
--   voice_doc          / <filename.md>          (a line_anchor — фраза-якорь)
--   voice_profile      / <slug>                 (например anna_product)
--   voice_profile_field/ <slug>:<field>         (например anna_product:antipatterns)
--   lexicon_question   / <phrase>               (фраза из lexicon.json)
--   lexicon_metaphor   / <phrase>
--   forbidden_phrase   / <group_id>:<phrase>    (например fem_esoteric:наш круг)
--   forbidden_topic    / <topic_id>             (например diagnoses)
--   segment            / <slug>
--   psycho_type        / <slug>
--   channel            / <slug>
--   content_form       / <slug>
--
-- verdict: эмоциональная оценка («что я хочу видеть в v_next»).
-- status:  жизненный цикл заметки.

CREATE TABLE IF NOT EXISTS source_annotations (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    therapist_id          UUID NOT NULL REFERENCES therapists(id),

    source_type           TEXT NOT NULL,
    source_id             TEXT NOT NULL,
    line_anchor           TEXT,            -- сниппет строки (выживает edit'ы doc'а)

    verdict               TEXT NOT NULL
                          CHECK (verdict IN ('good', 'bad', 'fix', 'neutral')),
    comment               TEXT,

    status                TEXT NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'addressed', 'wontfix')),
    addressed_in_version  TEXT,            -- например 'v3_draft.md' / 'lexicon v2'

    author                TEXT NOT NULL DEFAULT 'UI',
    created_at            TIMESTAMP NOT NULL DEFAULT NOW(),
    addressed_at          TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_source_annot_source
    ON source_annotations (source_type, source_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_source_annot_status
    ON source_annotations (therapist_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_source_annot_verdict
    ON source_annotations (verdict, status);
