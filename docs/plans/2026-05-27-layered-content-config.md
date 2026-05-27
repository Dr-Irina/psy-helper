# Plan: гибкая параметризация контент-генератора (Phase 1 — данные)

## Context

После получения **Audience Research** от Анны появились критические новые вводные, которые меняют архитектуру до того, как мы начали писать content engine:

1. **Курс «Академия Супружества» — два автора**: Анна + Оксана. Нужен `joint` voice (пока — placeholder, Оксаны материалов нет, придут позже).
2. **Продуктовый стиль ≠ лекторский.** В лекциях Анна — на «ты», с матом, провокативно. В продуктовом контенте — на «Вы», без мата, спокойно, взрослый тон. Это **два разных голоса**.
3. **Audience Research даёт 3 новых классификатора контента** (помимо уже сделанных topics + hunt_stages):
   - `target_segment` (4 сегмента: Усталая жена / На грани / Стелю соломку / Эмиграция)
   - `target_psycho_type` (4 типа: Аналитик / Тёрпеливая / Импульсивная / По рекомендации)
   - `target_channel` (TG / IG / email / Reels / подкаст / эфир / звонок)
4. **Antipatterns языка** из исследования: «женственность», «истинная природа», «гармоничные отношения», «девушка-плюс», «наш круг», «гарантия результата» — должны блокироваться в генерации.
5. **Главный сегмент v1**: «Усталая жена». **Главный психотип**: «Тёрпеливая». 70% контента — для пары.
6. Архитектурно сразу делаем **multi-tenant ready**: имя «Анна» нигде не хардкодим, всё параметризовано (для будущих экспертов в фабрике).

Цель: подготовить полную инфраструктуру конфигурации голоса/аудитории/канала **до** того, как пишем content engine v0, чтобы движок сразу знал про все эти параметры и не пришлось рефакторить.

**Это только Phase 1 (данные).** Content engine v0 (генератор) — отдельная следующая задача.

---

## Approach: Universal generator + Layered Configuration

**Главный принцип:** один универсальный generator entry point, который принимает любую комбинацию voice profile + channel/format + audience-параметров. Любой новый тип контента (от SMS до длинного лендинга) добавляется как **один YAML в `data/channels/`** — без изменений в движке.

Каждая генерация = композиция 5 слоёв + параметры:

```
Layer 1: Voice Profile   КТО говорит   (anna_lecture | anna_product | joint_product | … | custom inline)
Layer 2: Segment Overlay КОМУ говорим   (1-4: tired_wife | on_edge | planning | emigration | none)
Layer 3: Psycho Overlay  ЧЕМ зацепить  (1-4: analyst | patient | impulsive | referral | none)
Layer 4: Channel Overlay ГДЕ публикуем (tg_post | tg_story | insta_post | insta_reel |
                                        tiktok_video | youtube_short |
                                        email_body | email_subject | call_script |
                                        lp_section | lp_hero | faq_item |
                                        sms | push_notif | ad_copy |
                                        webinar_pitch | podcast_intro | bio_text |
                                        carousel_slide | … расширяется свободно)
Layer 5: Content Form    КАК структурировано (storytelling | case_study | tutorial |
                                              tips_list | opinion | educational |
                                              quote_card | provocation | quiz |
                                              metaphor_explain | …расширяется)
+ Parameters:            hunt_stage (1-5), topics[], optional subtopic
+ Optional preset:       именованный набор всех значений (для частых комбинаций)
+ Optional inline overrides: для one-off случаев, без создания YAML
```

Layer 4 (ГДЕ) и Layer 5 (КАК) — **ортогональны**: одна и та же история-сторителлинг может быть упакована и в TG-пост, и в TikTok, и в email, и в секцию лендинга. Один и тот же канал поддерживает много форм (TG-пост может быть и сторителлингом, и tips_list).

Hierarchy of overrides: **profile defaults → preset → channel defaults → explicit params → inline overrides**.

Generator API (концепт для Phase 2):
```python
generate(
    voice_profile_slug = "anna_product",     # любой из data/voice_profiles/
    channel_slug       = "tiktok_video",     # любой из data/channels/
    content_form_slug  = "storytelling",     # любой из data/content_forms/
    segment_slug       = "tired_wife",       # optional
    psycho_type_slug   = "patient",          # optional
    hunt_stage         = 1,                  # optional
    topics             = ["marriage"],       # optional
    topic_hint         = "границы в браке",  # optional
    inline_overrides   = {                    # optional, для one-off
        "max_length": 800,
        "extra_instructions": "сделай 3 варианта заголовка",
    },
) → ContentDraft (markdown + provenance + metadata)
```

**Подчёркиваю:** для нового типа контента → один YAML в `data/channels/`. Если нужен ad-hoc формат — `inline_overrides` без YAML вообще. Один и тот же движок обрабатывает всё.

Хранение конфигов: YAML/JSON в `data/`. В git, легко итерировать. Pydantic dataclass'ы для загрузки (в Phase 2 при написании генератора). БД-расширение — отложено.

---

## Конкретные артефакты

### 1. Audience data (распарсить из `docs/Audience Research.md`)

```
data/audience/
  segments/
    1_tired_wife.yaml      # Усталая жена — главный сегмент
    2_on_edge.yaml          # На грани
    3_planning.yaml         # Стелю соломку
    4_emigration.yaml       # Эмиграция
  psycho_types/
    1_analyst.yaml          # Аналитик
    2_patient.yaml          # Тёрпеливая — главный тип
    3_impulsive.yaml        # Импульсивная
    4_referral.yaml         # По рекомендации
  competitors.json          # 5 конкурентов с позиционированием
  market_gaps.json          # незанятые ниши (для suggest_topics в Phase 2)
  segment_type_matrix.json  # пересечение сегментов и типов с приоритетами
  positioning.md            # УТП-формула + главные сообщения по сегментам
```

Поля для сегмента (по структуре Audience Research §2):
- `slug, name, age_range, marriage_years_range, situation, triggers[]`
- `pain_phrases[]` — её слова: «он меня не слышит», «живём как соседи»
- `objections[]`
- `decision_speed, willing_to_pay_range`
- `main_channels[], supplemental_channels[], content_formats[], active_hours`
- `main_message` — слоган-крючок для генератора

Поля для психотипа (§3):
- `slug, name, motivator, decision_speed`
- `attracts[], repels[]`
- `best_formats[], cta_examples[]`

### 2. Voice profiles

```
data/voice_profiles/
  anna_lecture.yaml      # «ты» + мат точечно — для подкастов/лекций/живых эфиров
  anna_product.yaml      # «Вы» + без мата + спокойный взрослый — для постов/email/курса
  joint_product.yaml     # placeholder: anna_product + joint markers + позиционирование Академии
```

Шаблон YAML:
```yaml
slug: anna_product
name: Анна (продуктовый)
author: Anna
form_of_address: "Вы"
register: продуктовый
mat_allowed: false
sources:
  voice_doc: data/voice_document/v2_draft.md
  lexicon: data/style/lexicon.json
  raw_quotes:
    path: data/style/raw_quotes.jsonl
    filter:
      remove_mat: true
      max_quotes: 8
antipatterns:
  - "женственность"
  - "истинная природа"
  - "гармоничные отношения"
  - "девушка-плюс"
  - "наш круг"
  - "наши девочки"
  - "гарантия результата"
description: >
  Анна в продуктовом регистре. Спокойный, взрослый тон.
  Признаёт сложность реальности. Личный, тёплый. Без пафоса.
```

`joint_product.yaml` — аналогично, но `author: "Anna + Oksana"`, поле `placeholder: true`, в `description` явно сказано «пока опирается только на материалы Анны + общие правила Академии Супружества; обновится когда Оксана даст материалы».

### 3. Channel overlays (ГДЕ) — открытый список

Стартовый набор (10 шт), **список открытый** — любой новый канал добавляется одним YAML без изменений в движке:

```
data/channels/
  # Старт (Phase 1)
  tg_post.yaml          # 600-1200 chars, hook первая строка, CTA опционально
  tg_story.yaml         # короткое, кружочек-friendly
  insta_post.yaml       # 1000-2000 chars, эмоциональный заход
  insta_reel.yaml       # 15-45 sec script: hook + body + CTA
  tiktok_video.yaml     # 15-60 sec; hook первые 1.5 сек, тренды, разговорный пересказ
  email_subject.yaml    # 35-60 chars, без клик-бейта
  email_body.yaml       # 500-1500 chars, persona, conversational
  podcast_intro.yaml    # ~30 sec spoken
  call_script.yaml      # 5 min ~600 words, beats структура
  carousel_slide.yaml   # один слайд карусели (один генерим N раз для full carousel)

  # Понадобятся дальше — каждый добавляется одним YAML
  # youtube_short.yaml      # шортсы YouTube, аналогично reels/tiktok с поправкой на платформу
  # youtube_video_long.yaml # сценарий длинного видео 8-15 мин
  # lp_section.yaml         # секция лендинга
  # lp_hero.yaml            # hero-блок лендинга
  # faq_item.yaml           # вопрос/ответ FAQ
  # webinar_pitch.yaml      # анонс вебинара
  # webinar_full_script.yaml # сценарий часового вебинара
  # sms.yaml                # короткие 160 chars
  # push_notif.yaml         # пуш-уведомление
  # ad_copy.yaml            # рекламный текст для таргета
  # bio_text.yaml           # описание для bio в соцсетях
  # threads_post.yaml       # длинная цепочка в Threads/Twitter
```

Поля YAML: `slug, format, max_length, min_length, hook_style, cta_required, voice_form_default, prompt_template_extra`.

Если нужен **one-off** формат — генератор принимает `inline_overrides` напрямую в вызове, без создания YAML.

### 3b. Content forms (КАК) — открытый список

Нарративные/структурные формы контента, ортогональные каналу:

```
data/content_forms/
  # Старт (Phase 1)
  storytelling.yaml       # завязка → конфликт → решение → мораль; есть герой
  case_study.yaml         # разбор конкретного кейса клиента (анонимизированный!)
  tutorial.yaml           # пошагово: как сделать Х (шаг 1, 2, 3...)
  tips_list.yaml          # 3-7 коротких советов/принципов списком
  opinion.yaml            # резкая позиция автора, аргументация, провокация дискуссии
  educational.yaml        # объяснение термина/феномена с примерами
  quote_card.yaml         # одна короткая мощная фраза с минимальным контекстом
  provocation.yaml        # вопрос-вызов, заставляющий читателя усомниться
  quiz.yaml               # вопрос с вариантами ответов (для сторис/постов)
  metaphor_explain.yaml   # объяснение сложного через расширенную метафору
  
  # Дальше при необходимости
  # contrarian.yaml         # «вы все думаете Х, а на самом деле Y»
  # behind_the_scenes.yaml  # «как это устроено у меня»
  # before_after.yaml       # клиентка до/после
  # myth_busting.yaml       # развенчание мифа
  # personal_story.yaml     # личная история эксперта
```

Поля YAML: `slug, name, structure_template, hook_style, requires_hero (bool), example_outline`.

**Combinatorial coverage:** 10 channels × 10 content_forms = 100 базовых комбинаций без написания нового кода — каждая может быть применена к любому segment × psycho_type × hunt_stage × voice_profile. Это та гибкость, которая нужна.

### 4. Forbidden_phrases (расширение существующего файла)

`data/style/forbidden_topics.json` → version 2: добавить секцию `phrases`:
```json
{
  "version": 2,
  "topics": [...],         // 6 существующих категорий не трогаем
  "phrases": [             // новое
    {
      "id": "fem_esoteric",
      "label": "Эзотерический женский сленг",
      "phrases": ["женственность", "истинная природа", "гармоничные отношения",
                  "женская/мужская энергия", "женская природа"],
      "applies_to": ["product", "joint_product"],   // в lecture не блокируется
      "reason": "Отталкивает главный сегмент (Audience Research §1)"
    },
    {
      "id": "sect_tone",
      "label": "Сектоподобная риторика",
      "phrases": ["наш круг", "наши девочки", "девушка-плюс", "девушка-минус"],
      "applies_to": ["all"],
      "reason": "Audience Research §1, отстройка от конкурентов"
    },
    {
      "id": "guarantee_claims",
      "label": "Гарантии результата",
      "phrases": ["гарантия результата", "100% результат", "спасу любой брак", "точно поможет"],
      "applies_to": ["all"],
      "reason": "Этика + Audience Research §1"
    }
  ]
}
```

### 5. Memory (новые записи)

Создать 4 новых файла в `~/.claude/projects/-Users-irina-bugorkova-Desktop-dev-psy-helper/memory/`:
- `project_anna_oksana_academy.md` — Аня работает с Оксаной, продукт «Академия Супружества»
- `project_voice_registers.md` — два регистра: лекторский (ты+мат) и продуктовый (Вы+без мата)
- `project_main_audience.md` — главный сегмент = «Усталая жена», главный тип = «Тёрпеливая», их пара = 70% контента
- `project_language_antipatterns.md` — список запрещённых слов из Audience Research

Обновить `MEMORY.md` индекс.

### 6. tech_spec_marketing_funnel.md (обновление)

- Расширить §3 (архитектура) — добавить layered config диаграмму (5 layers)
- Расширить §6 (CRM) — добавить ссылки на segments/psycho_types артефакты
- Расширить §10 (структура кода) — добавить `data/voice_profiles/`, `data/audience/`, `data/channels/`, `data/content_forms/`, `data/presets/`
- Добавить §X «Voice profile layers и Audience overlays» — описать как генератор будет компоновать слои (включая TikTok, storytelling, etc.)
- Дописать в §17 (открытые вопросы) — материалы Оксаны pending

### 7. CLAUDE.md (gateway-документ проекта — критично для не-потери контекста)

CLAUDE.md загружается автоматически в каждую новую сессию. Сейчас в нём нет упоминания Audience Research, voice registers, layered config — всё это пропадёт между сессиями без явного обновления.

Что добавить в CLAUDE.md:

- Раздел **«Контент-генератор: что построено в инфраструктуре»** — короткий, со ссылками на:
  - `tech_spec_marketing_funnel.md` (полное ТЗ воронки)
  - `docs/Audience Research.md` (источник сегментов/типов/конкурентов)
  - `data/voice_profiles/` (3 профиля голоса)
  - `data/audience/` (4 сегмента + 4 типа + конкуренты + позиционирование)
  - `data/channels/` (10 каналов включая TikTok)
  - `data/content_forms/` (10 форм, включая storytelling)
  - `data/style/` (lexicon, raw_quotes, forbidden_topics v2)
  - `data/voice_document/v2_draft.md` (лекторский voice-doc, ждёт ревью Ани)
- Раздел **«Архитектурные принципы контент-генератора»**:
  - Layered config: 5 слоёв (voice / segment / psycho_type / channel / content_form) + параметры
  - Universal generator: один entry point на любой тип контента
  - Multi-tenant ready: имя «Анна» нигде не хардкодим, всё параметризовано
  - Два регистра: лекторский (ты+мат) и продуктовый (Вы+без мата)
  - Anna + Oksana = Академия Супружества (joint voice, Оксанин корпус pending)
- Расширить раздел **«Решения и feedback от пользователя»** ссылками на новые memory-файлы про антипаттерны, регистры, главный сегмент.

CLAUDE.md обновление = **первый шаг Phase 1**, страховка от потери контекста, если сессия прервётся в середине.

---

## Critical files

| Файл | Действие |
|---|---|
| `docs/Audience Research.md` | READ ONLY — источник |
| `data/voice_document/v2_draft.md` | существующий, voice profile ссылается |
| `data/style/lexicon.json` | существующий, voice profile ссылается |
| `data/style/raw_quotes.jsonl` | существующий, voice profile фильтрует |
| `data/style/forbidden_topics.json` | MODIFY → v2 с phrases |
| `data/audience/**` | CREATE (новая папка, 4+4+3 файла + positioning) |
| `data/voice_profiles/**` | CREATE (3 YAML) |
| `data/channels/**` | CREATE (10 YAML — включая tiktok_video) |
| `data/content_forms/**` | CREATE (10 YAML — storytelling, case_study, и т.д.) |
| `tech_spec_marketing_funnel.md` | MODIFY |
| `~/.claude/projects/.../memory/MEMORY.md` + 4 новых memory | CREATE / MODIFY |

**НЕ затрагиваем:** `db/migrations/`, `psy_helper/`, `scripts/streamlit_app.py`, `pyproject.toml`. Никаких миграций БД и кода в этой Phase 1.

---

## Verification

После завершения Phase 1:

1. **Структурная целостность YAML/JSON**:
   ```bash
   python3 -c "import yaml; import glob; [yaml.safe_load(open(f)) for f in glob.glob('data/voice_profiles/*.yaml') + glob.glob('data/audience/**/*.yaml', recursive=True) + glob.glob('data/channels/*.yaml')]; print('all yaml ok')"
   ```
2. **Контентная целостность**: ручная сверка по чек-листу против `docs/Audience Research.md`:
   - 4 сегмента ✓
   - 4 психотипа ✓
   - 5 конкурентов ✓
   - все pain_phrases / main_message сохранены ✓
3. **Forbidden_topics v2** грузится: проверить через простой Python `json.load`.
4. **Memory** видна: `ls ~/.claude/projects/.../memory/ | grep -c .md` ≥ 9 (5 старых + 4 новых).
5. **tech_spec_marketing_funnel.md** содержит секцию про layered config: `grep "layered" tech_spec_marketing_funnel.md`.

---

## Что НЕ в этой задаче

- Content engine v0 (генератор) — следующая задача (task #8), на готовой инфраструктуре
- Sample generations — туда же
- Streamlit UI с параметрами — после v0 движка
- Перенос voice-doc v2 в БД с `is_active` — отложено
- Сбор материалов Оксаны — pending, отдельная задача
- Миграция БД для voice_profiles — пока не нужна, YAML достаточно
- Pydantic dataclass'ы для config — будут в Phase 2 (с генератором, не сейчас)

---

## Cost оценка

- Anthropic API: **$0** (никаких LLM-запросов в этой задаче)
- Claude Code (моя работа): ~$0.5-1.5 overage (несколько Write + Edit + парсинг файла)

---

## Implementation order (для Phase 1)

**Порядок выбран так, чтобы переживание контекста было максимальным даже при прерывании в середине.** Сначала — самое важное для не-потери: CLAUDE.md, memory, tech_spec. Потом — артефакты.

1. **CLAUDE.md** — добавить разделы «Контент-генератор: что построено» + «Архитектурные принципы» + ссылки на новые memory. **Это первое — страховка от потери контекста.**
2. **4 новых memory-файла** + обновить MEMORY.md (Anna+Oksana, voice registers, главный сегмент, antipatterns).
3. **tech_spec_marketing_funnel.md** — обновить с layered config (5 слоёв), сегментами, content forms.
4. Распарсить `docs/Audience Research.md` → 4 segments + 4 psycho_types + competitors + gaps + matrix + positioning.
5. Записать как YAML/JSON в `data/audience/`.
6. Создать 3 voice profile YAML в `data/voice_profiles/`.
7. Создать 10 channel overlay YAML в `data/channels/` (включая `tiktok_video.yaml`).
8. Создать 10 content_form YAML в `data/content_forms/` (включая `storytelling.yaml`).
9. Обновить `data/style/forbidden_topics.json` → v2 с `phrases` секцией.
10. Verify (см. секцию выше).
11. Закрыть task #2/#3? Нет — оставить как pending для Анны/пользователя.
12. Создать новые задачи в TaskList: «Получить материалы Оксаны» и «Content engine v0 на layered config».
13. **Опционально:** скопировать сам план из `~/.claude/plans/graceful-mixing-flurry.md` в `docs/plans/2026-05-27-layered-content-config.md` — чтобы он попал в git и пережил сессии (планы в `~/.claude/plans/` локальны и временны).
