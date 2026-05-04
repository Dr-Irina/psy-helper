# psy-helper

AI-ассистент конкретного психолога **Анны**. **Не** AI-психолог — цифровой помощник для базы знаний по её методу и сопровождения клиентов между сессиями.

**Каноническая спецификация: `tech_spec.md`** (текущая v5.2). Это источник истины. Перед любыми архитектурными изменениями — сверяйся с ТЗ.

---

## Текущее состояние (актуально на момент последнего апдейта файла)

**MVP-0 ядро завершено + расширенный корпус в работе.**

| Метрика | Значение |
|---|---|
| Лекций транскрибировано | **68** |
| Смысловых блоков (clean_segments) | **1073** |
| Концептов (concepts) | **3919** |
| Эмбеддингов (1024-dim e5) | для всех segments + concepts |
| Voice-document | v1 (черновик), активен в БД |
| Streamlit UI | работает на http://localhost:8501 |
| Гибридный поиск | BM25 + vector + RRF |
| Всё локально | да, кроме `claude -p` для batch |

**Что сделано:** транскрипция (Mac CPU + Windows GPU) → смысловая сегментация (claude -p) → извлечение концептов 9 типов (claude -p) → эмбеддинги локально → ingest в Postgres → гибридный поиск → Streamlit UI с 4 вкладками (поиск / по типам / по лекциям / похожие).

**Что осталось до полного MVP-0 по ТЗ:**
- Встреча с Анной по review-файлу (артефакты готовы в `data/review_for_meeting.md` + `docs/architecture.md`)
- Voice-document v2 после ревью

**Что НЕ сделано (отложено):**
- Граф концептов с явными рёбрами (опция 5 из обсуждения с пользователем)
- Кластеризация в темы (опция 3)
- Перевод batch-скриптов с `claude -p` на Anthropic API (важно: нужно сделать перед массовыми re-runs, см. ниже)

---

## Стек

Python 3.11 / Postgres 16 + pgvector / Redis / WhisperX (Whisper large-v3 + pyannote-3.1) / sentence-transformers (intfloat/multilingual-e5-large, 1024-dim) / Streamlit. **Деплой пока не настроен** (по ТЗ — Coolify на Hetzner).

LLM-обработка (сегментация / концепты / voice-doc) сейчас идёт через `claude -p` (subprocess к Claude Code CLI). Для прода и больших batch'ей **планируется переход на Anthropic API**.

---

## Структура репо

```
psy-helper/
├── tech_spec.md                            # ТЗ — источник истины
├── CLAUDE.md                               # этот файл
├── docs/
│   ├── architecture.md                     # схема для Анны (без техжаргона)
│   ├── architecture-technical.md           # схема для разработчика
│   ├── architecture.html                   # рендер с mermaid → PDF
│   ├── voice_document_v1.html
│   └── review_for_meeting.html
│
├── pyproject.toml
├── Dockerfile                              # CPU (Mac, Linux без GPU)
├── Dockerfile.cuda                         # GPU (Windows + NVIDIA)
├── docker-compose.yml                      # postgres + redis + ui (Streamlit)
├── docker-compose.cuda.yml                 # override для GPU
├── .dockerignore                           # исключает data/, models/
│
├── db/migrations/
│   ├── 001_init.sql                        # полная схема ТЗ §6
│   └── 002_hybrid_search.sql               # tsvector + GIN индексы
│
├── psy_helper/
│   ├── pipelines/transcribe.py             # WhisperX + pyannote (с monkey-patch'ами, см. ниже)
│   ├── db/connection.py                    # psycopg helper
│   ├── search.py                           # hybrid_search_concepts/segments через RRF
│   └── taxonomy.py                         # 9 типов концептов (canonical)
│
├── scripts/
│   ├── # === Транскрипция (Whisper, локально CPU/GPU) ===
│   ├── transcribe.py                       # одиночный файл
│   ├── batch_transcribe.py                 # все из data/lectures/, идемпотентно
│   │
│   ├── # === Сегментация и концепты (через claude -p, host) ===
│   ├── segment_via_claude.py               # raw.json → segments.json
│   ├── extract_concepts_via_claude.py      # raw.json+segments.json → concepts.json
│   ├── generate_voice_doc_via_claude.py    # → data/voice_document/v1_draft.md
│   │
│   ├── # === Загрузка в БД (docker, идемпотентно) ===
│   ├── init_db.py                          # применить миграции
│   ├── ingest_raw.py                       # raw.json → raw_transcripts
│   ├── ingest_segments.py                  # segments.json → clean_segments
│   ├── ingest_concepts.py                  # concepts.json → concepts (с merge)
│   ├── ingest_voice_doc.py                 # markdown → voice_document с автоверсионированием
│   │
│   ├── # === Эмбеддинги (docker, локальная модель) ===
│   ├── embed_segments.py                   # → segment_embeddings
│   ├── embed_concepts.py                   # → concepts.embedding
│   │
│   ├── # === Артефакты для людей ===
│   ├── render_markdown.py                  # raw.json → читабельный transcript.md
│   ├── render_digest.py                    # → concepts_digest.md + per-lecture digest.md
│   ├── render_review.py                    # → review_for_meeting.md (чекбоксы)
│   ├── render_html.py                      # md+mermaid → standalone HTML для печати в PDF
│   │
│   └── # === UI ===
│   └── streamlit_app.py                    # 4 вкладки: поиск / по типам / по лекциям / похожие
│
└── data/                                   # gitignored
    ├── lectures/                           # исходные .m4a, ~71 файл, ~26 ГБ
    ├── transcripts/<lecture>/
    │   ├── raw.json                        # WhisperX вывод
    │   ├── metadata.json                   # параметры обработки
    │   ├── transcript.md                   # читабельный
    │   ├── segments.json                   # смысловые блоки (Claude)
    │   ├── concepts.json                   # концепты (Claude)
    │   └── digest.md                       # сводка
    ├── voice_document/v1_draft.md
    ├── concepts_digest.md
    └── review_for_meeting.md
```

---

## Полный пайплайн обработки лекции (что → чем → куда)

```
audio (.m4a)
  ↓ scripts/batch_transcribe.py            (Whisper локально, GPU/CPU)
data/transcripts/<lecture>/raw.json
  ↓ scripts/segment_via_claude.py          (claude -p, host)
data/transcripts/<lecture>/segments.json
  ↓ scripts/ingest_raw.py + ingest_segments.py    (docker → Postgres)
raw_transcripts + clean_segments
  ↓ scripts/extract_concepts_via_claude.py        (claude -p, host)
data/transcripts/<lecture>/concepts.json
  ↓ scripts/ingest_concepts.py                    (docker → Postgres, dedupe by therapist+name)
concepts (с source_segments[] обратной ссылкой на clean_segments)
  ↓ scripts/embed_segments.py + embed_concepts.py (docker, локальная модель e5-large)
segment_embeddings + concepts.embedding (1024-dim)
  ↓ Streamlit UI (scripts/streamlit_app.py) или прямые SQL
поиск + просмотр
```

**Для всего пайплайна на новом наборе лекций:**
```bash
# 1. На Windows (GPU): транскрипция всех новых .m4a
docker compose -f docker-compose.yml -f docker-compose.cuda.yml run -d --name psy-batch app python scripts/batch_transcribe.py

# 2. На Mac (host): сегментация + концепты через claude -p
python3 scripts/segment_via_claude.py
python3 scripts/extract_concepts_via_claude.py

# 3. На Mac (docker): загрузка в БД + эмбеддинги
docker compose run --rm app python scripts/ingest_raw.py --therapist-name "Анна"
docker compose run --rm app python scripts/ingest_segments.py
docker compose run --rm app python scripts/ingest_concepts.py
docker compose run --rm app python scripts/embed_segments.py
docker compose run --rm app python scripts/embed_concepts.py
```

Все скрипты идемпотентные — пропускают уже обработанное.

---

## Известные monkey-patch'и в `psy_helper/pipelines/transcribe.py`

В верху файла два патча, которые **не убирать** до перехода на стабильные версии:

1. **`torch.load(weights_only=False)`** — PyTorch 2.6+ переключил дефолт на `True`, чекпоинты pyannote с omegaconf-объектами не проходят. Lightning явно передаёт `weights_only=True`, потому force-override.

2. **`hf_hub_download` rename** — pyannote.audio 3.4 внутри вызывает `hf_hub_download(use_auth_token=...)`, новый huggingface_hub этот kwarg удалил. Конвертируем `use_auth_token` → `token` на лету.

Также в `load_models()` есть `inspect.signature` проверка для DiarizationPipeline — старые/новые whisperx используют разные имена параметра HF-токена.

---

## Таксономия концептов (9 типов, **не выдумывать новые**)

См. `psy_helper/taxonomy.py`. Согласовано с пользователем:
- `term` — терминология метода
- `technique` — приёмы, ходы
- `claim` — утверждения, принципы
- `warning` — предостережения, red flags (важно для safety!)
- `recommendation` — книги, ресурсы, авторы
- `exercise` — практики, упражнения
- `question` — фирменные вопросы для рефлексии
- `metaphor` — метафоры, образы
- `example` — кейсы, иллюстрации

При extraction Claude иногда придумывает `reference` (фактически recommendation) — починено пост-обработкой. См. историю фиксов в коммитах.

---

## Зависимости и сервисы

```
docker-compose.yml services:
  - postgres   (pgvector/pgvector:pg16, port 5432)
  - redis      (redis:7-alpine, port 6379)
  - ui         (Streamlit, port 8501) — включается явно через `docker compose up -d ui`
  - app        (build by Dockerfile, для одиночных run)

Volumes:
  - postgres-data
  - redis-data
  - models-cache  (HF cache: Whisper + pyannote + e5-large)
```

`.env` (gitignored, шаблон в `.env.example`):
```
HF_TOKEN=...                       # для pyannote, нужны принятые лицензии:
                                   #   pyannote/speaker-diarization-3.1
                                   #   pyannote/segmentation-3.0
                                   #   pyannote/speaker-diarization-community-1
POSTGRES_USER=psy
POSTGRES_PASSWORD=psy
POSTGRES_DB=psy_helper
# Опционально для Whisper:
# WHISPER_DEVICE=cuda
# WHISPER_COMPUTE_TYPE=float16
```

---

## Жёсткие принципы из ТЗ — соблюдать буквально

1. **Не AI-психолог.** Бот не ведёт терапию, не диагностирует, не работает с острыми клиентами. Анна — супервизор и ответственное лицо.
2. **MVP-стратегия (ТЗ §1a):** MVP-0 справочник → MVP-1 case-study на разработчике-клиенте + клиентский граф → MVP-2 безопасные режимы бота → MVP-3 голос + GraphRAG + открытый чат → MVP-4 масштаб. Между этапами — gates.
3. **Двойная роль разработчик-клиент (ТЗ §1b):** только «закрытые» сессии (через N мес после записи + явное закрытие Анной), право вето, ежемесячная сверка состояния терапии. Архитектурно встроено в схему `raw_transcripts.therapy_status`.
4. **ТЗ §14 «Критичные НЕ»** — буквально.
5. **Юридические/регуляторные вопросы отложены** пользователем — не пушить.

---

## Решения и feedback от пользователя (важно для будущих сессий)

- **Психолога зовут Анна** — не Анастасия, не другие имена. Используется в БД (`therapists.name = 'Анна'`).
- **Никогда не добавлять `Co-Authored-By: Claude` в commit-сообщения.** Только содержательное сообщение.
- **Не пушить на legal** в обсуждениях.
- **Таксономия концептов** = 9 типов фиксированных, новых не придумывать.
- Memory с этими и другими решениями: `~/.claude/projects/-Users-irina-bugorkova-Desktop-dev-psy-helper/memory/`.

---

## Cost / billing — ВАЖНО (накопилось $147+ за 3 дня)

**Откуда расходы:**
- 95% сообщений в этой сессии шли с >150k context — Claude Code считает это «long context» и автоматически списывает из **Extra usage / overage** ($85/мес у пользователя по умолчанию)
- Weekly limit подписки **не задействуется** на long context
- За 3 дня сожрали $71+ из $85 overage

**Что делать:**
1. **Перенести batch-скрипты на Anthropic API key** (`segment_via_claude.py`, `extract_concepts_via_claude.py`, `generate_voice_doc_via_claude.py`). Тогда все массовые обработки идут из отдельного бюджета (где можно поставить hard-limit).
2. **Использовать `/compact` регулярно** в интерактивных сессиях.
3. **Не читать большие файлы целиком** (raw.json по 4 МБ, листинги, многоэкранные tracebacks). Брать только хвосты.
4. **Снизить Extra usage cap** в Claude Code Settings → Usage.

---

## Полезные команды

```bash
# Локально на Mac (UI)
docker compose up -d postgres redis ui
open http://localhost:8501

# Публичная ссылка через ngrok (временная, $0 free tier)
ngrok http 8501

# БД-запросы напрямую
docker exec -it psy-helper-postgres-1 psql -U psy -d psy_helper

# Свежие digest и review-файлы
python3 scripts/render_digest.py
python3 scripts/render_review.py
python3 scripts/render_html.py docs/architecture.md
```

---

## Где смотреть статус данных глазами

| Что | Файл | Назначение |
|---|---|---|
| Полный список концептов по типам | `data/concepts_digest.md` | Обзор всего корпуса |
| Концепты для встречи с Анной | `data/review_for_meeting.md` (только 2 лекции; нужно перерендерить) | Чекбоксы для разметки |
| Per-лекция: блоки + концепты | `data/transcripts/<lecture>/digest.md` | Читать как книгу |
| Voice-document v1 | `data/voice_document/v1_draft.md` | Черновик для ревью Анны |
| Архитектура для Анны | `docs/architecture.md` (+ HTML) | На встречу |
