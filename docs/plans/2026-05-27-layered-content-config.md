# Plan: Phase 2 — Content Engine v0 + Streamlit UI + git-ifying data/configs

> **Updated 2026-05-27 (self-review v2):** добавлены 8 CRITICAL пробелов, 5 HIGH улучшений, исправлены 3 архитектурные ошибки. См. секцию «Self-review: что было пропущено» в конце.

## Context

Phase 1 (инфраструктура layered config) завершена и закоммичена. Готово:
- 3 voice profiles (`anna_lecture`, `anna_product`, `joint_product` placeholder)
- 4 audience segments + 4 psycho_types (из Audience Research)
- 10 channels (включая TikTok, carousel_slide)
- 10 content forms (включая storytelling)
- 3919 концептов размечены по 9 топикам + 5 ступеням Ханта (Haiku Batch)
- Voice-doc v2 (лекторский) сгенерирован
- Style corpus + lexicon + forbidden_topics v2 (с антипаттернами Audience Research)

Phase 2 — **Universal Content Engine v0 + UI**:
- Сборка промтов из 5 слоёв (voice/segment/psycho/channel/form) + параметры
- Anthropic API + prompt caching + provenance
- БД для сохранения черновиков (для analytics и UI)
- CLI + Streamlit UI (Аня может сама нажимать кнопки)

Параллельно — **хозяйственная задача**: расширить `.gitignore`, чтобы конфигурационные артефакты в `data/` попадали в git (whitelist подход). Сейчас всё `data/` игнорируется → при clone на другую машину пропадают voice_profiles, audience, channels, content_forms, lexicon, raw_quotes, voice-doc.

---

## Approach

### Часть A: Git whitelist для data/ (отдельный первый коммит)

Меняю `.gitignore` с blacklist на whitelist для `data/`. В git идут:

```
data/voice_profiles/         # 3 YAML голоса
data/audience/               # сегменты, психотипы, конкуренты, gaps, positioning
data/channels/               # 10 YAML каналов
data/content_forms/          # 10 YAML форм
data/style/lexicon.json
data/style/raw_quotes.jsonl  # ← по выбору пользователя, max режим
data/style/forbidden_topics.json
data/voice_document/*.md     # черновики voice-doc
```

Остаются gitignored (большие, генерируемые, операционные):
```
data/lectures/               # ~26 GB audio
data/transcripts/            # ~гигабайты raw.json + промежуточных
data/concepts_digest.md      # пересобирается из БД
data/review_for_meeting.md   # пересобирается
data/classify_state.json     # operational
data/voice_doc_state.json    # operational
data/classify_samples/       # тестовые выгрузки
data/voice_document/_input_for_claude.md  # tmp
models/                      # HF cache
```

**После этого коммита**: clone репо → docker compose up → init_db → можно сразу пытаться генерить (нужны только raw_transcripts/clean_segments/concepts в БД, которые отдельно ingest'ятся из transcripts; либо БД-дамп).

### Часть B: Content Engine v0

#### B.1. `pyproject.toml` — добавить deps
- `pydantic>=2.0` (для config dataclasses; не уверена что уже стоит — проверить)
- `pyyaml>=6.0` (для загрузки YAML конфигов)

#### B.2. Миграция `db/migrations/004_content_drafts.sql`

```sql
CREATE TABLE IF NOT EXISTS content_drafts (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  therapist_id        UUID NOT NULL REFERENCES therapists(id),    -- multi-tenant (CRITICAL #2)
  voice_profile_slug  TEXT NOT NULL,
  channel_slug        TEXT NOT NULL,
  content_form_slug   TEXT NOT NULL,
  segment_slug        TEXT,
  psycho_type_slug    TEXT,
  hunt_stage          INTEGER,
  topics              TEXT[],
  topic_hint          TEXT,
  content             TEXT NOT NULL,
  provenance          JSONB,             -- { "c123": concept_id, "s456": segment_id }
  -- Версионирование промта и конфигурации (CRITICAL #1) — критично для A/B и регрессий
  prompt_version      TEXT NOT NULL,     -- "v0.1.0" или sha-хеш собранного промта
  config_snapshot     JSONB NOT NULL,    -- snapshot всех 5 layers + параметров на момент генерации
  -- Cost tracking (CRITICAL #3)
  model               TEXT NOT NULL,     -- "claude-sonnet-4-6" | "claude-haiku-4-5"
  cost_usd            NUMERIC(10, 4),
  tokens_input        INT,
  tokens_output       INT,
  cache_creation_tokens INT,
  cache_read_tokens   INT,
  -- PII flag (CRITICAL #5)
  pii_flags           TEXT[],            -- ["suspicious_name:Маша", "phone:..."] — для review
  -- Lifecycle
  status              TEXT NOT NULL DEFAULT 'draft',  -- draft|approved|published|rejected|failed
  reviewed_by         TEXT,
  review_notes        TEXT,
  created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
  reviewed_at         TIMESTAMP,
  generation_duration_ms INT,           -- для мониторинга latency
  failure_reason      TEXT              -- если status='failed'
);

CREATE INDEX IF NOT EXISTS idx_content_drafts_therapist_status
  ON content_drafts (therapist_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_drafts_filter
  ON content_drafts (voice_profile_slug, channel_slug, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_drafts_segment_stage
  ON content_drafts (segment_slug, hunt_stage);
-- Для diversity: быстрый поиск past drafts по (channel, segment, topics)
CREATE INDEX IF NOT EXISTS idx_content_drafts_diversity
  ON content_drafts (channel_slug, segment_slug, created_at DESC);
```

#### B.3. `psy_helper/content_gen/` модуль

```
psy_helper/content_gen/
├── __init__.py
├── config.py           # Pydantic-модели для всех 5 layers + GenerationConfig
├── loaders.py          # load_voice_profile(slug), load_segment, ...
│                       # LRU-кэш на process lifetime
├── retrieval.py        # wrapper над psy_helper/search.py с фильтрами по
│                       # concepts.topics + concepts.hunt_stages
├── diversity.py        # NEW (HIGH #11) — anti-similarity к past drafts из content_drafts
├── prompts.py          # base template + per-form modifiers
│                       # (АРХ. ошибка C — не один монолитный template)
├── generator.py        # entry point: generate(config) → ContentDraft
│                       # + Map-Reduce для каналов с requires_map_reduce=true (CRITICAL #4)
│                       + streaming support (HIGH #12)
├── validators.py       # provenance check, forbidden filter, term_replacements
├── pii.py              # NEW (CRITICAL #5) — regex на known names, phones, emails
├── cost.py             # NEW (CRITICAL #3) — calculate_cost(usage, model) из Anthropic
│                       # response.usage с поддержкой cache_creation/cache_read токенов
├── few_shot.py         # NEW (HIGH #10) — self-improving loop: подмешивает approved
│                       # драфты из content_drafts как few-shot examples
├── logging_config.py   # NEW (CRITICAL #8) — structured JSON logging через logging
└── storage.py          # save_draft / load_draft / update_status (БД)
```

**Композиция при генерации:**
```
1. Load VoiceProfile (anna_product) → form_of_address, mat_allowed,
                                       антипаттерны, цитаты, voice-doc
2. Load Segment (tired_wife) → main_message, pain_phrases, objections
3. Load PsychoType (patient) → attracts, repels, CTA-стиль
4. Load Channel (tg_post) → длина, hook_style, CTA_required
5. Load ContentForm (storytelling) → structure_template, requires_hero
6. Retrieval: гибридный поиск по corpus с фильтрами
   topics @> ARRAY[topic] AND :stage = ANY(hunt_stages) AND ts_rank на topic_hint
   → top-15 concepts + top-5 segments
7. Build system prompt (со всеми layers + retrieved + lexicon + forbidden)
   cache_control: ephemeral на stable prefix
8. Build user prompt (topic_hint или общая тема)
9. Anthropic API call (Sonnet 4.6, max_tokens по channel.max_length)
10. Parse response → провести валидацию (provenance + forbidden + replacements)
11. Save в БД (content_drafts), вернуть ContentDraft
```

**Структура system prompt** (концепт):
```
Ты помогаешь {author} писать {channel.name} в {register} регистре.

ГОЛОС {voice_profile.name}:
{voice_profile.description}
Форма обращения: {form_of_address}
Mat allowed: {mat_allowed}

СТИЛЬ РЕЧИ (сырые цитаты автора):
{raw_quotes отфильтрованные согласно voice_profile.sources.raw_quotes.filter}

ФИРМЕННЫЕ ФРАЗЫ (использовать минимум 2):
{lexicon.questions[:5] + lexicon.metaphors[:5]}

ЗАПРЕЩЕНО:
{voice_profile.antipatterns + forbidden_topics.phrases где applies_to in [all, register]}

АУДИТОРИЯ — сегмент «{segment.name}»:
Ситуация: {segment.situation}
Их слова о боли: {segment.pain_phrases}
Возражения: {segment.objections}
ГЛАВНОЕ СООБЩЕНИЕ: {segment.main_message}

ПСИХОТИП «{psycho_type.name}»:
Мотиватор: {psycho_type.motivator}
Цепляет: {psycho_type.attracts}
Отталкивает: {psycho_type.repels}

КАНАЛ: {channel.name}
Длина: {channel.length.optimal_chars}, max {channel.length.max_chars}
Hook: {channel.hook_style}
CTA: {channel.cta_required ? channel.cta_style : "опционально"}

ФОРМА: {content_form.name}
Структура: {content_form.structure_template}

МАТЕРИАЛ ИЗ КОРПУСА АНИ (с источниками):
{retrieved concepts in format: [c123] "name" — description}
{retrieved segments in format: [s456] from lecture X: summary}

ТРЕБОВАНИЯ:
- Каждое утверждение → footnote [^c123] (concept) или [^s456] (segment) из материала выше
- Минимум 2 фирменные фразы из lexicon
- {form_of_address} обращение строго
- Без запрещённых фраз
- НЕ выдумывать факты и формулировки

Верни ТОЛЬКО markdown черновика с footnotes. Без преамбулы.
```

#### B.4. CLI

**`scripts/generate_content.py`** — генерация одного куска:
```bash
docker compose run --rm app python scripts/generate_content.py \
  --voice anna_product \
  --channel tg_post \
  --form storytelling \
  --segment tired_wife \
  --psycho-type patient \
  --hunt-stage 2 \
  --topic marriage \
  --hint "границы в браке"
```

Сохраняет в БД, печатает текст с footnotes + ID черновика для следующего step.

**`scripts/suggest_topics.py`** — предлагает темы:
```bash
docker compose run --rm app python scripts/suggest_topics.py \
  --voice anna_product \
  --segment tired_wife \
  --psycho-type patient \
  --hunt-stage 2 \
  --limit 10
```

Выводит топ-10 идей тем (по комбинации частоты концептов в выбранных топиках/ступенях + diversity heuristic).

#### B.5. Streamlit UI (`scripts/streamlit_app.py` — расширение)

**Безопасность (CRITICAL #6, #7):**
- Password gate в начале приложения — простой env-var `STREAMLIT_PASSWORD`,
  без него `st.stop()`. Это не enterprise auth, но защита от случайного
  доступа на localhost / ngrok.
- Rate limit на генерацию: не более **N=10 генераций / 5 минут** на сессию.
  Счётчик в `st.session_state`. Превышение → блокирующий warning.

**Новая вкладка «🎨 Генератор»:**
- Левая колонка: dropdown'ы для voice_profile / channel / content_form /
  segment / psycho_type / hunt_stage / topic + text input для topic_hint.
- Кнопка «Сгенерировать» → API call → draft в правой колонке.
- **Streaming output** (HIGH #12) — текст появляется по мере приёма от
  Anthropic SDK (не «крутилка» на 30 сек).
- Footnotes кликабельны → раскрывают исходный concept/segment.
- **PII warning panel** (CRITICAL #5) — если `pii_flags` непустой,
  показать сверху draft'a красную плашку «Найдены подозрительные слова:
  [список]. Проверь вручную.»
- **Cost panel** — после генерации показывает реальный $ списания
  + cumulative за сессию.
- Кнопки: «✓ Одобрить» / «✎ Править» / «🔄 Сгенерировать ещё вариант» / «✗ Отклонить».
- «Сгенерировать ещё вариант» создаёт новую запись в content_drafts с тем
  же config — для A/B сравнения (MEDIUM, см. отложенное).

**Новая вкладка «📋 Черновики»:**
- Таблица с фильтрами (статус / voice / channel / сегмент / дата / therapist).
- Inline просмотр + смена статуса + комментарии Анны.
- Показывает $ cost на черновик + cumulative за период.
- Экспорт в clipboard.
- Опционально: «🔍 Diff между двумя версиями» — выделить два draft'a → diff.

#### B.6. Архитектурные правки (см. self-review)

**Pre-Phase-2 fixes — нужны до старта движка:**

- **A) Product-version voice-doc на «Вы».** Текущий v2_draft.md написан
  «от первого лица на ты» (лекторский). Использование его как «семантики»
  в anna_product → утечка «ты»-формулировок. → перегенерить разделы 1,
  2, 4, 5 на «Вы» через Sonnet ($0.20). Сохранить как
  `data/voice_document/v2_product_draft.md`. anna_product.yaml ссылается
  на эту версию вместо v2_draft.md. Лекторская версия остаётся для
  anna_lecture.

- **B) `lexicon_min` per content_form.** Добавить в каждый
  `data/content_forms/*.yaml` поле `lexicon_min: int` —
  обязательный минимум фирменных фраз. Значения:
  - quote_card / sms / push_notif / email_subject: `0`
  - tiktok_video / insta_reel: `1`
  - остальные: `2`
  Generator подставляет это значение в промт «использовать минимум
  {lexicon_min} фирменных фраз».

- **C) Base + per-form prompt templates.** Не один монолитный template.
  В `prompts.py`:
  - `BASE_TEMPLATE` — voice / audience / forbidden / retrieved material
    (одинаков для всех)
  - `FORM_MODIFIERS` — dict `{form_slug: extra_instructions_text}` с
    специфичными hint'ами (storytelling: «начни с Setup», quiz: «дай
    варианты ответов нумерованным списком», и т.д.)
  - Final prompt = BASE_TEMPLATE + "\n\n" + FORM_MODIFIERS[form_slug].

#### B.7. Model-per-channel (HIGH #9)

Поле `preferred_model` в каждом `channel.yaml`:
- `claude-haiku-4-5` для: sms, push_notif, email_subject, quote_card,
  bio_text, tg_story (~5× дешевле, качество сравнимо для коротких)
- `claude-sonnet-4-6` для: всех остальных (где важна стилистика)

Generator выбирает модель по channel.preferred_model. Overrideable через
`--model haiku|sonnet` в CLI или inline_overrides.

### Часть C: Smoke test + iterate

После B готовности:
1. Сгенерить 10 драфтов с разнообразием (3 voice × 4 канала × 4 формы выборочно)
2. Оценить вручную по 5 метрикам из `tech_spec_marketing_funnel.md` §15
3. Решить что доработать:
   - **Если retrieval плох** → добавить reranking (BGE локально или Cohere API)
   - **Если в драфтах опасные утверждения** → добавить LLM-as-judge перед save
   - **Если стиль не угадан** → добавить few-shot с настоящими постами Анны (task #3)
4. Закрыть task #8

---

## Critical files

| Файл | Действие |
|---|---|
| `.gitignore` | MODIFY — whitelist для data/ конфигов |
| `pyproject.toml` | MODIFY (+ pydantic, pyyaml если нет) |
| `db/migrations/004_content_drafts.sql` | CREATE |
| `psy_helper/content_gen/__init__.py` | CREATE |
| `psy_helper/content_gen/config.py` | CREATE — Pydantic-модели для всех 5 layers |
| `psy_helper/content_gen/loaders.py` | CREATE — YAML→Pydantic |
| `psy_helper/content_gen/retrieval.py` | CREATE — wrapper над search.py с фильтрами |
| `psy_helper/content_gen/diversity.py` | CREATE — anti-similarity к past drafts (HIGH #11) |
| `psy_helper/content_gen/prompts.py` | CREATE — base template + per-form modifiers (Арх C) |
| `psy_helper/content_gen/generator.py` | CREATE — entry point + Map-Reduce (CRITICAL #4) + streaming (HIGH #12) |
| `psy_helper/content_gen/validators.py` | CREATE — provenance + forbidden + replacements |
| `psy_helper/content_gen/pii.py` | CREATE — regex/known-names filter (CRITICAL #5) |
| `psy_helper/content_gen/cost.py` | CREATE — calculate_cost(usage, model) (CRITICAL #3) |
| `psy_helper/content_gen/few_shot.py` | CREATE — self-improving loop (HIGH #10) |
| `psy_helper/content_gen/logging_config.py` | CREATE — structured JSON logging (CRITICAL #8) |
| `psy_helper/content_gen/storage.py` | CREATE — load/save в content_drafts |
| `data/voice_document/v2_product_draft.md` | CREATE — product-version voice-doc на «Вы» (Арх A) |
| `data/voice_profiles/anna_product.yaml` | MODIFY — ссылается на v2_product_draft.md |
| `data/content_forms/*.yaml` | MODIFY — добавить поле `lexicon_min` (Арх B) |
| `data/channels/*.yaml` | MODIFY — добавить поле `preferred_model` (HIGH #9) |
| `scripts/generate_content.py` | CREATE — CLI |
| `scripts/suggest_topics.py` | CREATE — CLI |
| `scripts/generate_product_voice_doc.py` | CREATE — для одноразовой генерации v2_product_draft.md |
| `scripts/streamlit_app.py` | MODIFY — +2 вкладки + auth + rate limit + streaming |
| `.env.example` | MODIFY — добавить `STREAMLIT_PASSWORD` |
| `tests/` | CREATE — unit tests для loaders + validators (HIGH #13) |

**Не трогаем:** существующие миграции 001/002/003, существующие скрипты пайплайна транскрипции/embedding/ingest, ratet existing `psy_helper/search.py` и `psy_helper/taxonomy.py`.

---

## Verification

1. **Git whitelist:** `git status` после первого коммита не показывает diff из `data/voice_profiles/*.yaml`, `data/audience/**/*.yaml`, `data/channels/*.yaml`, `data/content_forms/*.yaml`, `data/style/*.json|jsonl`, `data/voice_document/*.md`. Большие файлы (transcripts/, lectures/) остаются ignored.
2. **Pydantic load:** `python -c "from psy_helper.content_gen.loaders import load_voice_profile; print(load_voice_profile('anna_product'))"` → возвращает валидный объект, без warning'ов.
3. **Migration:** `init_db.py` применяет 004, таблица `content_drafts` создаётся.
4. **CLI smoke test:** `generate_content.py --voice anna_product --channel tg_post --form storytelling --topic marriage` → draft создан в БД, провенансовые footnotes ведут на реальные concept_id, без mat'а, форма обращения «Вы», нет запрещённых фраз.
5. **CLI suggest_topics:** возвращает 10 различных тем (без повторов) с поправкой на фильтры.
6. **Streamlit:** новые вкладки доступны, draft генерится через UI, footnotes раскрываются, кнопка «Одобрить» меняет статус в БД.
7. **Forbidden filter:** попытка `--topic "депрессия"` → блок с reason='diagnoses'.
8. **Term replacements:** «брак» в LLM-выходе автоматически заменяется на «супружество» (для voice_profile с term_replacements).

---

## Cost оценка Phase 2

| Что | Стоимость |
|---|---|
| Anthropic API: smoke test 10 драфтов | ~$1 (Sonnet + cache) |
| Дальше в production: 1 пост | ~$0.10 (с кэшем) |
| Дальше: 1 рилс/тикток | ~$0.05 |
| Дальше: 1 длинный email | ~$0.15 |
| Месячный бюджет (50 единиц контента) | ~$5-10 |
| Claude Code (моя работа) | ~$3-5 overage |

---

## Implementation order

**0. Hygiene: git whitelist для data/ (отдельный первый коммит)**
   - `.gitignore` whitelist → `git add data/...` → commit. Чисто хозяйственный.

**1. Pre-Phase-2 fixes (архитектурные правки, ~30 минут)**
   - 1a. Обновить все `data/content_forms/*.yaml` — добавить `lexicon_min` (Арх B)
   - 1b. Обновить все `data/channels/*.yaml` — добавить `preferred_model` (HIGH #9)
   - 1c. `scripts/generate_product_voice_doc.py` + запустить → `v2_product_draft.md` (Арх A, ~$0.20)
   - 1d. Обновить `data/voice_profiles/anna_product.yaml` → ссылается на v2_product_draft.md
   - Commit «pre-Phase-2: product voice-doc, lexicon_min, preferred_model»

**2. Foundation: deps + миграция БД + logging**
   - `pyproject.toml` (+ pydantic, pyyaml, structlog) + `docker compose build app`
   - `db/migrations/004_content_drafts.sql` (с therapist_id, prompt_version, config_snapshot, cost fields, pii_flags) + apply
   - `psy_helper/content_gen/logging_config.py` (CRITICAL #8)
   - `.env.example` — добавить `STREAMLIT_PASSWORD`

**3. Pure functions (без LLM)**
   - `config.py` + `loaders.py` — Pydantic + YAML loaders
   - `cost.py` (CRITICAL #3) — calculate_cost из anthropic usage
   - `pii.py` (CRITICAL #5) — regex на known names + phones + emails
   - `validators.py` — provenance + forbidden + replacements

**4. Unit tests (HIGH #13)**
   - tests/test_loaders.py — все 31 YAML грузятся в Pydantic
   - tests/test_validators.py — forbidden filter, term replacements, PII detection
   - tests/test_cost.py — calculate_cost для разных usage сценариев
   - Запустить: `docker compose run --rm app pytest`

**5. Retrieval + Prompts + Diversity**
   - `retrieval.py` — wrapper search.py с фильтрами concepts.topics + hunt_stages
   - `diversity.py` (HIGH #11) — query past drafts из БД, передаём в промт «избегай повторов с этими темами»
   - `prompts.py` — BASE_TEMPLATE + FORM_MODIFIERS (Арх C)

**6. Generator + Storage**
   - `storage.py` — save_draft (включая config_snapshot, prompt_version) / load_draft
   - `generator.py` — главный pipeline:
     - 6a. Базовый sync вариант
     - 6b. Streaming variant (HIGH #12)
     - 6c. Map-Reduce для каналов с requires_map_reduce=true (CRITICAL #4)
   - `few_shot.py` (HIGH #10) — pull approved drafts из БД, добавляем в промт

**7. CLI**
   - `scripts/generate_content.py`
   - `scripts/suggest_topics.py`
   - Smoke test 1 draft через CLI: anna_product + tg_post + storytelling + tired_wife + patient + hunt_stage=2 + topic=marriage → проверить:
     - Save в БД успешен
     - cost_usd > 0
     - pii_flags пустой (на тесте)
     - provenance footnotes ведут на реальные concept_id
     - Текст на «Вы», без мата, без forbidden фраз

**8. Streamlit UI**
   - Password gate (CRITICAL #6)
   - Rate limit в session (CRITICAL #7)
   - Вкладка «🎨 Генератор» со streaming + PII warning + cost panel
   - Вкладка «📋 Черновики» с фильтрами

**9. Smoke test через UI** → 1 draft, проверить весь UX.

**10. Тестовый прогон 10 драфтов** с разнообразием параметров. Записать subjective и objective метрики.

**11. Оценка с пользователем и Аней** → решить что доработать (reranking? LLM-as-judge? больше few-shot? per-channel модель прокачать?)

**12. Commit Phase 2** — по логическим частям (foundation / pure functions / generator / UI), не одним коммитом.

**13. Закрыть task #8 как completed.**

---

## Что НЕ в Phase 2 (отложено)

### Технические
- **Reranking** (Cohere/BGE) — если retrieval окажется плох
- **LLM-as-judge** перед output — если в драфтах опасные утверждения
- **A/B-тестирование** (MEDIUM #14) — генерация 3 variants одного config'a
- **Compare side-by-side** в UI (MEDIUM #15)
- **Persistent cache 1h TTL** Anthropic вместо ephemeral 5-min (MEDIUM #16)
- **Monitoring dashboard** (Grafana / dbt) — Anthropic balance, latency, cache hit rate
- **Backup БД** — pg_dump расписание (когда поедем на Hetzner)
- **Voice TTS** для аудио-постов в её голосе

### Продуктовые
- **Mini-product proposer** (task #9) — отдельная Phase 3
- **Material Оксаны → joint_product real assembly** — task #11, pending от пользователя
- **CRM / leads / sequences** (Layer 3-4 воронки) — позже
- **Multi-tenant onboarding нового эксперта** — продукт-фаза
- **Pre-publish to social networks** (Insta/TG API) — позже
- **TG bot для доставки драфтов Ане** (MEDIUM #17) — без full posting integration, просто «получи в чат, одобри кнопкой»
- **Webhook / API endpoint** для n8n/Zapier интеграций
- **Calendar / scheduling** — generation tied to publishing schedule
- **Анна как операционный пользователь** UI — после её ревью v0 + согласования
- **Аналитика что конвертит** — после того как контент пойдёт на каналы
- **Preview как пост в соцсети** (markdown→HTML render для визуала)
- **Локализация / английский язык** — пока только русский

---

## Безопасный fallback

Если Anthropic API упадёт в момент генерации (rate limit / timeout) — скрипт сохраняет state с failed_at, можно повторить позже. БД-запись помечается `status='failed'`, поле `failure_reason` заполняется (timeout / rate_limit / context_too_long / etc).

Если retrieval ничего не находит (например, новый topic без концептов в корпусе) → возвращаем ошибку «недостаточно материала», без выдумывания.

Если PII filter находит подозрительные имена → draft сохраняется со `status='draft'` + `pii_flags`, но в UI выводится красная плашка «требует ручной проверки».

---

## Self-review: что было пропущено (2026-05-27, v2 плана)

После написания v1 плана сделан критический пересмотр. Нашлось ~25 пробелов. Все CRITICAL и HIGH **уже встроены выше**, MEDIUM/NICE-TO-HAVE — в «Что НЕ в Phase 2». Этот раздел — для справки и для возможного «давайте сделаем X сейчас».

### CRITICAL (встроены в план)

| # | Что | Где в плане |
|---|---|---|
| 1 | Версионирование промта (prompt_version + config_snapshot) | B.2 (миграция) |
| 2 | therapist_id в content_drafts (multi-tenant) | B.2 |
| 3 | Cost tracking из Anthropic response | B.3 (cost.py), B.2 (поля) |
| 4 | Map-Reduce для webinar_full / video_long | B.3 (generator.py), step 6c |
| 5 | PII filter (regex + known names) | B.3 (pii.py), B.2 (pii_flags), UI |
| 6 | Streamlit auth (password gate) | B.5, .env.example |
| 7 | Rate limit на UI (10/5min) | B.5 |
| 8 | Structured logging вместо print | B.3 (logging_config.py) |

### HIGH (встроены)

| # | Что | Где |
|---|---|---|
| 9 | Model-per-channel (Haiku для коротких) | B.7, channels yaml |
| 10 | Self-improving few-shot loop | B.3 (few_shot.py), step 6 |
| 11 | Diversity-penalty к past drafts | B.3 (diversity.py), step 5 |
| 12 | Streaming output в Streamlit | B.5, step 6b |
| 13 | Unit tests для loaders + validators | tests/, step 4 |

### Архитектурные ошибки v1 (исправлены)

| Буква | Что было не так | Как исправлено |
|---|---|---|
| A | v2_draft.md (лекторский, на «ты») используется в anna_product | Pre-Phase-2 step 1c: генерим `v2_product_draft.md` на «Вы» |
| B | «минимум 2 фирменные фразы» — universal, но для quote_card/SMS не подходит | `lexicon_min` в content_form.yaml, B.6/step 1a |
| C | Один монолитный prompt template | BASE_TEMPLATE + FORM_MODIFIERS, B.6/step 5 |

### MEDIUM / NICE-TO-HAVE (отложены явно)

См. секцию «Что НЕ в Phase 2». Это пункты, до которых дойдём если v0 работает и есть бюджет.

### Что я **продолжаю не учитывать** в плане (известные пробелы)

Эти вещи я знаю что не покрыты, но не считаю критичными для v0. Записываю явно, чтобы не забыть:

- **Audit log** для изменения voice_profile / forbidden — частично решается через git (вот почему data/ в git правильно), но без UI-уровня tracking.
- **Email/Slack alerting** при ошибках генерации — для prod, не для localhost dev.
- **Backup БД** — pg_dump расписание, когда поедем на Hetzner.
- **Per-therapist изоляция конфигов** — сейчас `data/voice_profiles/` общий, для multi-tenant нужно `data/<therapist_slug>/`. Откладываем до второго эксперта.
- **Versioning voice profiles** через файлы → git уже даёт это (через `git log` и blame).
- **Regression tests** — snapshot testing для generation. Дорого, отложить.
- **Template variables в email** (`{first_name}`) — требует CRM, в Phase 2 не нужно.
- **Calendar integration** — для расписания постов, Phase 3+.

### Что в плане **уязвимо к изменениям требований**

Эти решения сделаны на основе текущего понимания, могут радикально измениться:
- **Модель Sonnet 4.6 для всего длинного** — если Haiku 4.5 в практике покажет сравнимое качество для постов, можно перевести 80% на Haiku и сэкономить 3-4x.
- **Streamlit как UI** — если Аня хочет TG-бот вместо веб-приложения, переделаем.
- **БД для черновиков** — если решим что нужна интеграция с notion / Airtable / sheets, БД станет один из источников, не единственный.
