# Status Phase 2 — Content Engine v0 (handoff для следующей сессии)

**Last update:** 2026-05-27, конец сессии
**Plan:** `docs/plans/2026-05-27-layered-content-config.md` (полный план Phase 2)
**Active task:** #8 «Прототип content engine v0 (TOFU: общий контент)»

---

## Где мы остановились

**✅ Сделано (Step 0 + Step 1 из плана Phase 2):**

| Step | Что | Commit |
|---|---|---|
| 0 | Git whitelist для `data/` — все конфиги контент-генератора в git | `3403f6d` |
| 1 | Pre-Phase-2 fixes: v2_product_draft.md + lexicon_min + preferred_model | `8947f36` |

**Текущее состояние коммитов:**
```
8947f36 feat: pre-Phase-2 — product voice-doc, lexicon_min, preferred_model
3403f6d chore: git whitelist для data/configs
4b96c45 docs: Phase 1 layered config — CLAUDE.md gateway, tech_spec extension
8d30530 feat: фундамент content engine — Audience Research, classification, voice-doc v2
```

Branch `main`, **5 commits ahead** of `origin/main` (не push'или, по правилу).

---

## ⏳ Дальше — Step 2-13 (новая сессия начинает отсюда)

Все шаги в `docs/plans/2026-05-27-layered-content-config.md` секция «Implementation order».

**Логические блоки:**

1. **Foundation (Step 2)** — `pyproject.toml` (+ pydantic, pyyaml, structlog), миграция 004_content_drafts.sql, logging_config.py, `.env.example` (+ `STREAMLIT_PASSWORD`).
2. **Pure functions (Step 3)** — `psy_helper/content_gen/{config,loaders,cost,pii,validators}.py`. Без LLM, чистые тесты.
3. **Unit tests (Step 4)** — `tests/test_loaders.py`, `tests/test_validators.py`, `tests/test_cost.py`. Через `pytest` в docker.
4. **Retrieval + Prompts + Diversity (Step 5)** — `retrieval.py` (wrapper search.py), `diversity.py`, `prompts.py` (BASE + FORM_MODIFIERS).
5. **Generator + Storage (Step 6)** — `generator.py` (с Map-Reduce + streaming), `storage.py`, `few_shot.py`.
6. **CLI (Step 7)** — `scripts/generate_content.py`, `scripts/suggest_topics.py`. Smoke test 1 draft.
7. **Streamlit UI (Step 8)** — 2 новые вкладки + password gate + rate limit + streaming + PII warning.
8. **Smoke test через UI (Step 9)** — 1 draft end-to-end.
9. **Тестовый прогон 10 драфтов (Step 10)** — разнообразие параметров, оценить с пользователем.
10. **Оценка с Аней (Step 11)** — что доработать (reranking? judge? few-shot?).
11. **Commit Phase 2 (Step 12)** — по логическим блокам.
12. **Закрыть task #8 (Step 13).**

---

## Что важно знать новой сессии

### Архитектурный recap (быстро войти в контекст)

- **Universal generator** на 5 слоёв: `voice_profile × segment × psycho_type × channel × content_form` + параметры (`hunt_stage`, `topics`, `topic_hint`, `inline_overrides`).
- Один CLI/API/UI на все типы контента: TG-пост, TikTok, рилс, email, лендинг, звонок, и т.д.
- **Multi-tenant ready** — никакого хардкода имени «Анна», всё параметризовано через `therapist_id` и slug'и.
- **Two-author**: Аня + Оксана = «Академия Супружества». Voice profiles `anna_lecture` / `anna_product` / `joint_product` (placeholder до материалов Оксаны).
- **Два регистра**: лекторский (на «ты», с матом) vs продуктовый (на «Вы», без мата). 70% контента — продуктовый, под главный сегмент «Усталая жена» + психотип «Тёрпеливая».

### Что **уже** готово к использованию

| Где | Что |
|---|---|
| БД (Postgres) | 3919 концептов с `topics[]` + `subtopics[]` + `hunt_stages[]` (миграция 003) |
| БД (Postgres) | `clean_segments` (1073 блока) + `raw_transcripts` (68 лекций) + embeddings |
| `data/voice_profiles/` | 3 YAML (anna_lecture, anna_product, joint_product) |
| `data/audience/` | 4 segments + 4 psycho_types + competitors + market_gaps + positioning |
| `data/channels/` | 10 YAML с `preferred_model` (haiku/sonnet) |
| `data/content_forms/` | 10 YAML с `lexicon_min` (0/1/2) |
| `data/style/` | lexicon.json (250+ метафор + 160+ вопросов Анны), raw_quotes.jsonl (15 длинных монологов), forbidden_topics.json v2 (6 категорий тем + 4 секции антипаттернов фраз) |
| `data/voice_document/` | v2_draft.md (лекторский) + v2_product_draft.md (продуктовый) |
| `psy_helper/search.py` | гибридный поиск BM25+vector+RRF (без фильтров по новым колонкам — добавить в retrieval.py) |
| Anthropic | API key в `.env`, workspace `psy-helper` с hard-limit, потрачено ~$7 за сессию |
| Docker | `app` image с `anthropic 0.104.1` + `pyyaml` (pydantic — добавить в Step 2) |

### Критические правила (memory)

Авто-загружаются из `~/.claude/projects/.../memory/`:
- Anna + Oksana = Академия Супружества, two-author
- Два регистра голоса (lecturer vs product)
- Главный сегмент «Усталая жена» + психотип «Тёрпеливая» (70% контента)
- Антипаттерны языка («женственность», «наш круг», «гарантия результата»)
- Терминология: «супружество», не «брак»
- Никакого транслита в коде/БД
- Credential hygiene при получении ключей в чате
- Никаких `Co-Authored-By: Claude` в коммитах

### Open questions для Анны / пользователя

См. `tech_spec_marketing_funnel.md` §17. Главное:
- **Task #2:** где живут 4500 контактов, какие инструменты Ани (email/TG/Insta)?
- **Task #3:** 5-10 настоящих постов Анны для few-shot
- **Task #11:** материалы Оксаны (лекции/посты) для пересборки joint_product

---

## Cost так far (за всю текущую сессию)

| Где | $ |
|---|---|
| Anthropic API (workspace psy-helper) | ~$7 |
| Claude Code overage (моя работа) | ~$8-10 (long-context почти весь день) |

Hard-cap workspace psy-helper: $20-30/мес. Запас ~$15.

---

## Что **точно** нельзя проеб...ть в новой сессии

1. **5 commits ahead, не push'или** — origin/main отстаёт. Не `git reset --hard` без backup.
2. **`.env` содержит ANTHROPIC_API_KEY** — gitignored, ОК, но не commit'ить и не дублировать в чате.
3. **План в `docs/plans/2026-05-27-layered-content-config.md`** — это источник истины для Phase 2. Если что-то непонятно — читать его, не выдумывать.
4. **CLAUDE.md** — обновлён, упоминает оба voice-doc (lecturer + product), все 5 layers, главный сегмент. Грузится автоматически.
5. **БД** — Postgres работает, миграции 001-003 применены. Миграция 004 (content_drafts) **ещё не создана** — это Step 2.

---

## Sanity-check перед стартом новой сессии

```bash
# 1. Подтверждение состояния git
git status --short  # должно быть пусто (working tree clean)
git log --oneline -6  # должно показывать 5 наших commits

# 2. БД работает
docker compose up -d postgres redis
docker exec psy-helper-postgres-1 psql -U psy -d psy_helper -c \
  "SELECT COUNT(*) FROM concepts WHERE topics IS NOT NULL;"
# Ожидание: 3919

# 3. YAML configs парсятся
docker compose run --rm app python -c "
import yaml, glob
for f in glob.glob('data/**/*.yaml', recursive=True): yaml.safe_load(open(f))
print('all yaml ok')
"

# 4. Anthropic SDK работает (без реального запроса)
docker compose run --rm app python -c "from anthropic import Anthropic; print(Anthropic.__name__)"
```

Если всё зелёное — стартовать Step 2.
