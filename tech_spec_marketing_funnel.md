# ТЗ: Маркетинговая воронка psy-helper

> Дополнение к основному `tech_spec.md` v5.2. Описывает промежуточную цель между MVP-0 и MVP-1: автоматизированная маркетинговая воронка для практики Ани (генерация контента под канал × ступень Ханта, профилирование клиентов, продажа мини-продукта → демо → основного курса).

**Версия:** v0.2 (расширено после встречи с Аней) · **Дата:** 2026-05-27 · **Статус:** черновик, требует утверждения после этапа A.

---

## 1. Цель и не-цель

**Цель.** Автоматизированная маркетинговая воронка для одиночной практики Ани:

- **Курс №1** (фокус первой версии воронки): **супружество**
- **Курсы №2+** (расширение в будущем): дети, подростки, и т.д. — каждый со своей тематической веткой воронки

Воронка покрывает все 5 ступеней лестницы Ханта (см. §2). Контент персонализирован по: топик × ступень × канал × сегмент.

**НЕ цель:**
- AI-психолог, бот-консультант для клиентов (MVP-2+ по основному ТЗ)
- Автономная публикация без manual review Ани
- Замена Ани как автора и эксперта — мы компонуем её слова и автоматизируем рутину, а не подменяем

---

## 2. Лестница Ханта = основная классификация системы

5 ступеней, **каждая запись в CRM имеет ступень, каждый кусок контента имеет одну или несколько ступеней:**

| # | Ступень | Состояние клиента | Тип контента/касания |
|---|---|---|---|
| 1 | Безразличие | «У меня всё нормально» | Провокационные посты, рилсы-крючки. «А ты замечаешь, что у вас уже не разговор?» |
| 2 | Осведомлённость | «Проблема есть, не знаю что делать» | Образовательные: «3 признака что у вас не конфликт, а ссора». Формируем проблему |
| 3 | Сравнение | «Ищу решение, выбираю подход» | Позиционирование метода: «почему КПТ-горизонт, а не классический психоанализ» |
| 4 | Выбор | «Подход понятен, кого выбрать» | Аня лично: кейсы, голос, видео, ценности, отзывы |
| 5 | Покупка | «Готов(а) платить» | Лендинг, цена, демо, личное предложение |

Без этой классификации персонализация невозможна. Это **центральная сущность** системы.

---

## 3. Архитектура (4 слоя)

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Knowledge base                                │
│  68 лекций · 3919 концептов · embeddings · voice-doc    │
│  + concepts.topics[] · concepts.subtopics[]             │
│  + concepts.hunt_stages [array]                         │
│  + lexicon · raw_quotes · forbidden_topics              │
└─────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 2: Content engine                                │
│  Input: topic × stage × channel × persona               │
│  Hybrid retrieval (BM25 + vector + RRF) — есть           │
│  Prompt: knowledge pack + style pack + Anthropic API     │
│  Output: post / email / call-script / mini-product idea │
│  + provenance (footnote → concept_id)                   │
└─────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 3: CRM + Profiling                               │
│  Lead (имя, контакты, ступень, теги, история касаний)   │
│  Импорт 4500 текущих + новые                            │
│  Дедупликация (email + tg_id + phone)                   │
│  AI-профилирование: классификация ступени, темы          │
│  Mini-consult dialog: AI-опросник → обновление профиля  │
└─────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 4: Channels + Sales orchestration                │
│  Adapters: TG bot / TG channel / email / Insta / phone   │
│  Sequences: 3 письма после подписки, ветка при отказе   │
│  Mini-product → демо → основной курс                    │
│  Analytics: что конвертит → метки в knowledge base       │
└─────────────────────────────────────────────────────────┘
```

Слои 1-2 — это то, что мы делаем **первыми**. Слой 3 разблокируется после получения текущей базы Ани. Слой 4 — после того, как 1-3 работают.

---

## 3a. Universal Generator + Layered Configuration (добавлено 2026-05-27)

После получения **Audience Research** от Анны добавлен ещё один уровень параметризации внутри Content engine (Layer 2 в §3). Главный принцип — **один universal generator entry point на любой тип контента**.

### 5 layers конфигурации

Каждая генерация = композиция 5 слоёв + параметры:

```
Layer 1: Voice Profile    КТО говорит    (anna_lecture | anna_product | joint_product | …)
Layer 2: Segment Overlay  КОМУ говорим   (tired_wife | on_edge | planning | emigration | none)
Layer 3: Psycho Overlay   ЧЕМ зацепить   (analyst | patient | impulsive | referral | none)
Layer 4: Channel Overlay  ГДЕ публикуем  (tg_post | insta_reel | tiktok_video | email_body |
                                          call_script | lp_section | carousel_slide | … расширяется)
Layer 5: Content Form     КАК структура  (storytelling | case_study | tutorial | tips_list |
                                          opinion | educational | quote_card | provocation |
                                          quiz | metaphor_explain | … расширяется)
+ Parameters:             hunt_stage (1-5), topics[], optional subtopic
+ Optional preset:        именованный набор всех значений (для частых комбинаций)
+ Optional inline overrides: для one-off случаев, без создания YAML
```

**Layer 4 и Layer 5 ортогональны:** одна и та же история-storytelling может быть упакована и в TG-пост, и в TikTok, и в email, и в секцию лендинга. Один и тот же канал поддерживает много форм.

### Хранение конфигов

```
data/voice_profiles/
  anna_lecture.yaml      # «ты» + мат точечно — для подкастов/лекций/живых эфиров
  anna_product.yaml      # «Вы» + без мата + спокойный — для постов/email/курса
  joint_product.yaml     # placeholder: anna_product + joint markers (Оксанин корпус pending)

data/audience/
  segments/{1_tired_wife, 2_on_edge, 3_planning, 4_emigration}.yaml
  psycho_types/{1_analyst, 2_patient, 3_impulsive, 4_referral}.yaml
  competitors.json · market_gaps.json · segment_type_matrix.json · positioning.md

data/channels/
  tg_post · tg_story · insta_post · insta_reel · tiktok_video · email_subject ·
  email_body · podcast_intro · call_script · carousel_slide  (расширяется)

data/content_forms/
  storytelling · case_study · tutorial · tips_list · opinion · educational ·
  quote_card · provocation · quiz · metaphor_explain  (расширяется)
```

### Generator API (концепт для v0)

```python
generate(
    voice_profile_slug = "anna_product",
    channel_slug       = "tiktok_video",     # или любой другой
    content_form_slug  = "storytelling",     # или любой другой
    segment_slug       = "tired_wife",       # optional
    psycho_type_slug   = "patient",          # optional
    hunt_stage         = 1,                  # optional
    topics             = ["marriage"],       # optional
    inline_overrides   = {...},              # optional, для one-off
) → ContentDraft (markdown + provenance + metadata)
```

**Hierarchy of overrides:** profile defaults → preset → channel defaults → explicit params → inline overrides.

**Multi-tenant ready:** имя «Анна» нигде в коде не хардкодим. Каждый эксперт (Аня, потом другие) — свой набор voice profiles + audience + (опционально) свой набор channels/content_forms.

---

## 4. Layer 1: Knowledge base

### 4.1. Уже есть
- `raw_transcripts` — 68 лекций
- `clean_segments` — 1073 блока + tsvector
- `concepts` — 3919 концептов 9 типов + embeddings 1024-dim
- `voice_document` v1 — устарел, требует регенерации
- `segment_embeddings`

### 4.2. Добавляем

| Артефакт | Где | Описание |
|---|---|---|
| `concepts.topics` | новая колонка в БД (text[]) | Multi-label из 9: marriage, partnership, children, teens, confidence, personal_effectiveness, finance, communication, general. Один разовый LLM-классификатор (Haiku + Batch) |
| `concepts.subtopics` | новая колонка в БД (text[]) | Open-ended теги (LLM генерирует на лету: «измены», «выгорание», «бунт подростка»). Канонизируем при ≥5 повторов |
| `concepts.hunt_stages` | новая колонка (int[]) | Одна или несколько из {1,2,3,4,5}. Один разовый LLM-разметчик |
| Voice-doc v2 | `voice_document` (новая запись) | Через Map-Reduce на 68 лекциях |
| Style corpus | `data/style/raw_quotes.jsonl` | Топ-15 длинных монологов Ани, доминантный спикер, ≥1500 знаков |
| Lexicon | `data/style/lexicon.json` | Фирменные фразы и метафоры (concepts type=question/metaphor + ручной обзор) |
| Forbidden topics | `data/style/forbidden_topics.json` | Минимальный расширяемый список |

### 4.3. Принцип разделения knowledge / style для генератора

Voice-doc — это **дистилляция**. LLM, делая выжимку, академизирует речь. Поэтому в промт генератора идут **два разных пакета**:

- **Knowledge pack**: voice-doc (формализованные принципы, red lines, техники), концепты по теме, forbidden_topics
- **Style pack**: сырые цитаты Ани, lexicon, few-shot её настоящих постов (когда придут)

Voice-doc остаётся как документ для людей. Стиль модели берёт из сырого материала.

---

## 5. Layer 2: Content engine

### 5.1. Что генерирует

Унифицированный engine, на вход — параметры:

```python
generate(
  topic="marriage",           # тематическая ветка
  hunt_stage=2,           # ступень Ханта
  channel="tg_post",      # tg_post, tg_story, insta_post, insta_reel, 
                          # email_subj, email_body, call_script, ...
  persona=None,           # для общего контента, или persona_id для персонализированного
  format_constraints={},  # длина, hook-формат, CTA и т.д.
  topic_hint="границы в браке",  # уточнение темы (опционально)
)
→ Draft (markdown + provenance + metadata)
```

### 5.2. Pipeline (для каждой генерации)

```
1. Retrieval: ищем по корпусу с фильтром topic + hunt_stages
   → top-N релевантных concepts + segments
2. Pack assembly:
   - Knowledge pack: voice-doc + retrieved + forbidden_topics
   - Style pack: raw_quotes + lexicon + (если есть) anna_posts + 
                 + few-shot контента того же channel × stage (когда накопится)
3. Prompt → Anthropic API (prompt caching на stable prefix)
4. Validation:
   - provenance check (все footnote'ы есть в БД)
   - forbidden filter (тема не в стоп-листе)
   - длина / формат для канала
5. Save → content_drafts (новая таблица)
6. Return draft for review
```

### 5.3. Suggest-topics режим

`scripts/suggest_topics.py [--topic marriage] [--stage 2]` — предлагает 10 тем, которые:
- Имеют материал в корпусе (≥3 концепта)
- Подходят под указанные topic + stage
- Не повторяют то, что уже сгенерировано (антидубль через `content_drafts.topic_hint`)

---

## 6. Layer 3: CRM + Profiling (будет после получения текущей базы)

### 6.1. Сущности

```sql
leads (
  id, name, email, tg_id, phone, instagram, 
  source,                  -- "import_2026", "tg_bot", "insta_dm", "email_subscribe"
  hunt_stage,              -- 1-5, обновляется AI
  topic_interest,          -- "marriage" | "children" | ...
  tags[],                  -- свободные метки
  last_touchpoint_at, 
  created_at
)

touchpoints (
  id, lead_id, channel, content_draft_id, 
  type,                    -- sent, opened, clicked, replied, ignored
  metadata jsonb, 
  created_at
)

profile_facts (             -- что AI узнал о клиенте
  id, lead_id, fact, source_touchpoint_id, confidence, created_at
)
```

### 6.2. Импорт 4500 текущих контактов

- Скрипт `scripts/import_leads.py` — формат зависит от того, где у Ани сейчас лежит база
- Дедупликация по email + tg_id + phone (нормализованные)
- Первичная классификация ступени и темы — пакетный LLM-прогон (~$1-3 разово)

### 6.3. Mini-consult: AI-опросник для профилирования

Короткий диалог (5-10 вопросов) в Insta DM / TG-боте:
- Стиль вопросов — фирменный Анны (используем тот же engine, режим `channel=mini_consult`)
- Каждый ответ → `profile_facts` + обновление `hunt_stage`
- В конце — рекомендация мини-продукта (когда будет) или CTA на демо

**Safety:** это **не консультация**, это маркетинговый опросник. Дисклеймер обязателен. Не работаем с кризисными состояниями (классификатор острых тем → перенаправление на «Аня свяжется лично»).

---

## 7. Layer 4: Channels + Sales orchestration

### 7.1. Adapters (порядок реализации)

| Канал | Когда делаем | Что нужно |
|---|---|---|
| TG bot | Этап 4 | TG Bot API + наш бот @anya_helper_bot |
| Email | Этап 4 | API текущего сервиса Ани (узнаём) |
| TG channel posting | Этап 5 | Запостить через бота-админа канала |
| Instagram | Этап 5 | Insta Graph API (для бизнес-аккаунта) или ручной экспорт |
| Phone scripts | Этап 5 | Текстовые сценарии для звонящего человека (Аня сама / помощник) |

### 7.2. Sequences

`sequence` — последовательность auto-касаний при триггере. Например:
- Trigger «подписался на TG-канал» → 3 поста-приветствия в DM (по одному через день)
- Trigger «прошёл mini-consult» → email с разбором профиля + предложение мини-продукта
- Trigger «не открыл 3 письма подряд» → re-engagement касание

Хранятся в `data/sequences/*.yaml`, исполняются воркером (Redis + RQ или Celery — выбрать на этапе).

### 7.3. Mini-product → Demo → Course

```
TOFU контент   →   mini-consult (бот)   →   mini-product (free/cheap)
                                                  │
                                                  ▼
                                            demo session (Аня)
                                                  │
                                                  ▼
                                              основной курс
```

Mini-product сам по себе — это **готовый контент**, генерируемый этим же engine'ом (PDF-гайд, чеклист, видеосценарий). Дизайн отдельно.

---

## 8. Layer X (cross-cutting): Mini-product proposer

Отдельная фича, помогает Ане **придумать**, что продавать как мини-ступеньку, через анализ корпуса:

```
scripts/suggest_mini_products.py --topic marriage

# Анализирует корпус по теме + ступеням 2-3 (где обычно лежат мини-продукты)
# Выдаёт 5-7 кандидатов:

1. "PDF: 7 фирменных вопросов для пары" 
   (из concepts type=question, отфильтр. по теме)
2. "Недельный челлендж: Через рот словами"
   (из concepts type=exercise + technique)
3. "Чек-лист: 5 признаков, что у вас ссора, а не конфликт"
   (из claim-концептов про конфликт)
4. "Мини-курс: Активное слушание в 3 шага"
   (technique + раздел из voice-doc)
5. "Тест: На какой ты ступени супружеской коммуникации"
   (внутри — лестница Ханта, замаскированная)
...
```

Аня выбирает 1-2 → запускаем полную генерацию мини-продукта (готовый PDF/последовательность писем).

---

## 9. Технологии

### 9.1. Anthropic API (не Claude Code subprocess)

- Отдельный бюджет с hard-limit (на console.anthropic.com)
- **Prompt caching** — voice-doc, style pack, lexicon как stable prefix → -90% на повторных
- **Batch API** — для voice-doc регенерации, тематизации, разметки ступеней (-50%, 24ч ok)
- Модели: claude-sonnet-4-6 для генерации, claude-haiku-4-5 для классификации / suggest

### 9.2. Уже есть в проекте
- Postgres 16 + pgvector
- Redis (для будущего sequence executor)
- `psy_helper/search.py` (BM25 + vector + RRF)
- Streamlit для UI
- WhisperX pipeline (для будущих лекций)

### 9.3. Добавляем (по слоям)

| Слой | Технологии |
|---|---|
| Knowledge | anthropic SDK, doc-классификаторы |
| Content engine | новый модуль `psy_helper/content_gen/` |
| CRM | новые таблицы Postgres, импорт-скрипты |
| Channels | python-telegram-bot, smtplib/SendGrid SDK/Mailchimp, аккуратно с Insta |
| Sequences | RQ или Celery |

---

## 10. Структура кода

```
psy_helper/
  content_gen/
    __init__.py
    retrieval.py        # обёртка search.py + фильтр по topic + stage
    voice_doc.py        # загрузка active voice-doc из БД
    style.py            # raw_quotes + lexicon + anna_posts
    forbidden.py        # forbidden_topics filter
    prompts.py          # шаблоны: post / email / call / mini_consult / mini_product
    generator.py        # entry point с prompt caching
    validators.py       # provenance, forbidden, format
  crm/
    __init__.py
    leads.py            # CRUD
    profiling.py        # AI-классификатор ступени / темы
    mini_consult.py     # диалог-опросник
  channels/
    __init__.py
    tg_bot.py
    email.py
    insta.py
    phone.py            # генерация скриптов
  sequences/
    __init__.py
    executor.py
    triggers.py

scripts/
  classify_concepts_topic.py            # тематизация
  classify_concepts_hunt_stage.py       # ступени
  regenerate_voice_doc_v2.py            # Map-Reduce + Batch
  build_style_corpus.py
  build_lexicon.py
  generate_content.py                   # CLI: --topic --stage --channel --topic-hint
  suggest_topics.py
  suggest_mini_products.py
  import_leads.py                       # 4500 контактов → leads
  start_sequence_executor.py            # воркер

data/
  style/
    raw_quotes.jsonl
    lexicon.json
    forbidden_topics.json
    anna_posts.jsonl                    # позже
  voice_document/
    v2_draft.md
  sequences/*.yaml
```

---

## 11. Этапы реализации (поэтапно сверху вниз воронки)

### Этап A — Подготовка knowledge base (1-2 недели)
- A.1. Anthropic API + hard-limit (блокер всего)
- A.2. Тематизация концептов (LLM-классификатор → `concepts.topic`)
- A.3. Разметка ступеней Ханта (LLM → `concepts.hunt_stages`)
- A.4. Voice-doc v2 через Map-Reduce + Batch
- A.5. Style corpus + lexicon + forbidden_topics
- A.6. (Парал.) Получить от Ани её настоящие посты для few-shot

### Этап B — Content engine v0 (TOFU) (1-2 недели)
- B.1. Модуль `psy_helper/content_gen/`
- B.2. CLI `generate_content.py` (post / email / call / mini_consult)
- B.3. CLI `suggest_topics.py` и `suggest_mini_products.py`
- B.4. Тестовый прогон: 10 постов TOFU на тему «брак» × ступень 1-2
- B.5. Streamlit-tab «Генерация»

### Этап C — Качество и итерация (1 неделя)
- C.1. Аня оценивает 10 черновиков по 5 метрикам (см. §15)
- C.2. Если стиль не угадан → добавить few-shot её постов
- C.3. Если retrieval плох → добавить reranking
- C.4. Если опасные утверждения → LLM-as-judge

### Этап D — CRM (зависит от получения базы Ани) (2-3 недели)
- D.1. Узнать состояние инструментов Ани
- D.2. Спроектировать схему миграции 4500 контактов
- D.3. Импорт-скрипт + дедупликация
- D.4. Первичная AI-классификация ступеней импортированных
- D.5. Streamlit-tab «CRM»

### Этап E — Mini-consult (1-2 недели)
- E.1. Опросник в чате (TG-бот или Streamlit)
- E.2. Извлечение profile_facts, обновление leads
- E.3. Финальная рекомендация в конце опросника

### Этап F — Channels (3-4 недели)
- F.1. TG-бот (приоритет — Анин канал, если есть)
- F.2. Email-интеграция (зависит от сервиса Ани)
- F.3. Insta-эксперимент (часто кончается полу-ручным режимом)

### Этап G — Sequences + Sales orchestration (2-3 недели)
- G.1. Sequence executor (Redis + RQ)
- G.2. Готовые YAML-сценарии (welcome / mini-product / re-engagement)
- G.3. Mini-product fulfillment (PDF/письма)
- G.4. Demo booking integration

### Этап H — Analytics + закрытый цикл обучения (2 недели)
- H.1. Дашборд: CTR / CR / revenue / ступени-распределение
- H.2. Метки эффективности обратно в knowledge base
- H.3. Re-training suggest-topics с приоритетом эффективных концептов

---

## 12. Cost оценки

| Операция | Стоимость | Частота |
|---|---|---|
| Тематизация концептов (one-time) | ~$1 (Batch) | Раз |
| Разметка ступеней Ханта (one-time) | ~$2 (Batch) | Раз |
| Voice-doc v2 (one-time) | ~$0.50 (Batch) | Раз |
| Импорт 4500 + классификация ступеней (one-time) | ~$3-5 (Batch) | Раз |
| Один пост / email / карусель | $0.05-0.10 (с cache) | Десятки в неделю |
| Mini-consult сессия (5-10 вопросов) | $0.20-0.50 | На каждого нового лида |
| Suggest-topics / mini-products | $0.10-0.30 | Раз в неделю |
| **Месячный бюджет на v0** | **~$20-50** | TOFU + light MOFU |
| **Месячный бюджет на полную воронку** | **~$100-200** | Зависит от потока лидов |

Hard cap на старте: $30/мес. Расширим, когда станет понятен реальный расход.

---

## 13. Safety

Психолог — safety-критичная область. Строгие правила.

### Минимум на v0 (содержательно)
- `forbidden_topics.json` — фильтр на этапе assembly
- Дисклеймер «черновик, требует ревью» в каждом выходе
- Provenance: каждое утверждение → источник
- Никакой автопубликации, всегда manual review Ани

### Forbidden topics (минимум, расширяемо)
- Диагнозы (депрессия, тревожность, расстройства)
- Острые состояния (суицид, кризис, ПТР)
- Конкретные клиенты Ани (по имени или узнаваемому описанию)
- Медицинские утверждения и гарантии результата
- «Анализ» чужих отношений по переписке
- Темы вне компетенции метода (расписать с Аней)

### Mini-consult safety
- Классификатор острых состояний на каждый ответ клиента
- При обнаружении → диалог обрывается, «Аня свяжется с вами лично» + контакт скорой помощи
- Все диалоги логируются для ревью

### Откладываем до проверки
- LLM-as-judge перед output
- Content moderation API
- Embedding-similarity check к voice-doc

---

## 14. Связь с основным ТЗ v5.2

Этот документ — **промежуточный шаг** между MVP-0 (готов) и MVP-1 (case-study). Согласован с пользователем 2026-05-20 и расширен 2026-05-27 после встречи с Аней.

После завершения этапов A-C (content engine TOFU) возвращаемся к решению: продолжать вглубь воронки (D-H) или временно переключаться на MVP-1 по основному ТЗ.

ТЗ §14 «Критичные НЕ» применяется буквально и расширяется в §13 этого документа.

---

## 15. Метрики качества

### Этап C (после первых 10 черновиков)

| Метрика | Шкала | Цель |
|---|---|---|
| Соответствие стилю Ани | 1-5 | ≥4 на 80% |
| Точность утверждений | 1-5 | ≥4 на 90% |
| Provenance корректен | да/нет | 100% |
| Запрещённые темы не затронуты | да/нет | 100% строго |
| Подходит для указанной ступени Ханта | 1-5 | ≥3 на 90% |
| Готов к публикации без правок | да/нет | ≥20% — амбициозно для v0 |

### Этапы D+ (когда воронка работает)
- CTR / Open rate / Reply rate по каналу × ступень
- Mini-consult completion rate
- TOFU → MOFU conversion
- MOFU → BOFU conversion
- Mini-product → demo conversion
- Demo → course conversion
- LTV сегмент Ани

---

## 16. Multi-tenancy / универсальность (фоном)

Этот документ описывает систему **для Ани**. Но по сути это **B2B SaaS для одиночных экспертов** (психологи, коучи, нутрициологи, юристы — все, у кого есть корпус контента и нет маркетинг-отдела).

**Что закладываем сейчас, чтобы не переписывать потом:**

1. Никаких `"Анна"` в коде. Имя терапевта — параметр / запись в БД (`therapists` table уже есть).
2. Тематическая структура `concepts.topic` — это уже multi-topic, легко переносится на multi-tenant.
3. Voice-doc, style corpus, lexicon, forbidden_topics — всё **per-therapist**. Хранить в БД (или в `data/<therapist_slug>/`).
4. Content engine — pure functions, на вход — knowledge + style + параметры. Без зашитой логики Ани.
5. Hunt stage классификация — универсальная, не специфична Ане.

**Что НЕ делаем сейчас (отложено до Product MVP):**
- Multi-tenant изоляция (auth, biz-rules)
- UI для онбординга нового эксперта
- Биллинг
- Public API

То есть архитектурно мы готовим почву, но фокус остаётся на Ане.

---

## 17. Открытые вопросы (требуют ответа от Ани или ясности у пользователя)

1. **Состояние текущей базы 4500 контактов** — где живёт, какие поля, как нормализовать. (Задача #2)
2. **Текущие инструменты Ани** — email-сервис, TG (канал/бот), Insta (бизнес/личный). (Задача #2)
3. **Мини-продукт** — придумываем через `suggest_mini_products.py` или у Ани уже есть идея?
4. **Большой курс про супружество** — уже продаётся / есть лендинг / цена / длительность?
5. **Кто оператор системы** — Аня сама / помощник-маркетолог / разработчик-оператор?
6. **Звонки** — кто звонит? Сама Аня / менеджер? Какой объём в неделю?
7. **Регистрация / биллинг** — Аня уже продаёт через какую платформу (GetCourse / Tinkoff Касса / ручное)?
8. **Тон в продающем контенте** — где её red lines? Что она НЕ хочет видеть как «слишком впаривание»?
9. **Расписание Ани** — какая интенсивность контента реалистична? Раз в день / 3 раза в неделю?
10. **Материалы Оксаны** — лекции / эфиры / посты для генерации её voice-doc и финального joint_product profile. Сейчас joint_product = placeholder на материале Анны.
11. **Настоящие посты Анны** для few-shot anna_product (Задача #3) — без них потолок качества стиля ниже.
12. **Канонизация subtopics** — после первых N генераций накопятся open-ended subtopic'и, которые часто повторяются → канонизировать при ≥5 упоминаниях.

Эти вопросы — артефакт, который кладётся в follow-up встречу с Аней.

---

## 18. Что НЕ берём в этот scope

Чтобы не плодить:
- GraphRAG (по основному ТЗ — MVP-3)
- Topic clustering (опция 3, отложено)
- Self-critique / reflection loop
- Multi-step agents (LangGraph и пр.)
- TTS / голосовая генерация
- Видео-генерация (только текстовые сценарии для рилсов)
- Соцсети auto-publish без ревью
- Public bot для отвечания клиентам как психолог (MVP-2+)
- Биллинг / платежи (используем внешние)
- Mobile app
- Multi-tenancy fully (готовим архитектуру, но не строим)