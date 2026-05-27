"""Регенерация voice-document v2 через Map-Reduce + Anthropic Batch API.

6 параллельных промтов (Sonnet 4.6), каждый выжимает один раздел voice-doc:
  1. Принципы работы          ← claim concepts
  2. Red lines (чего НЕ делаю) ← warning concepts
  3. Стилевая характеристика  ← топ-15 raw_quotes Анны
  4. Фирменные формулировки   ← question + metaphor concepts
  5. Подходы и техники        ← technique + exercise concepts
  6. Рекомендуемые источники  ← recommendation concepts

Reduce: механическая склейка в один markdown + статичный раздел 7
(«что дополнить из интервью с Анной»).

Output:
    data/voice_document/v2_draft.md

Далее (после ревью пользователем/Анной):
    docker compose run --rm app python scripts/ingest_voice_doc.py

Запуск в фоне:
    docker compose run -d --name psy-voice-doc app python scripts/regenerate_voice_doc_v2.py
    docker logs -f psy-voice-doc

State: data/voice_doc_state.json — на случай перезапуска.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from psy_helper.db.connection import connect

load_dotenv()

MODEL = "claude-sonnet-4-6"  # генерация связного текста, не классификация
STATE_PATH = Path("data/voice_doc_state.json")
OUT_PATH = Path("data/voice_document/v2_draft.md")
RAW_QUOTES_PATH = Path("data/style/raw_quotes.jsonl")
POLL_INTERVAL_S = 60


# === Промты для каждого раздела ===

PROMPT_PRINCIPLES = """Тебе дан список ВСЕХ claim-концептов (утверждений) психолога Анны из её 68 лекций — её базовые убеждения, принципы работы, картина мира.

Задача: выжать 7-10 ключевых принципов, которыми Анна руководствуется.

Правила:
- ОТ ПЕРВОГО ЛИЦА: «Я считаю, что…», «Я работаю исходя из того, что…»
- Каждый пункт — 1-2 предложения, конкретно, без абстракции
- Только то, что многократно подтверждается в разных claim-концептах
- Если не уверен — пиши `[нужно подтвердить с Анной]`
- НЕ выдумывай: одно упоминание — это частное мнение, не принцип

Терминология: используй «супружество», а не «брак» (это предпочтение Анны).

Верни ТОЛЬКО markdown-раздел в формате:

## 1. Принципы работы

1. **Я работаю исходя из того, что [принцип].** [пояснение в 1-2 предложениях]
2. **Я считаю, что [принцип].** [пояснение]
…

Начинай ПРЯМО с `## 1.`, без преамбулы, приветствий, послесловий."""


PROMPT_RED_LINES = """Тебе дан список ВСЕХ warning-концептов (предостережений) психолога Анны из её 68 лекций — то, что она настойчиво просит НЕ делать.

Задача: выжать 7-10 явных red lines (запретов) метода.

Правила:
- ОТ ПЕРВОГО ЛИЦА: «Я не…», «Я никогда не…», «Я не позволяю себе…»
- Каждый пункт — 1-2 предложения, конкретно
- Только если запрет повторяется в разных warning-концептах
- Если не уверен — `[нужно подтвердить с Анной]`

Терминология: «супружество», не «брак».

Верни ТОЛЬКО markdown:

## 2. Red lines (чего я НЕ делаю)

1. **Я не [что именно].** [пояснение почему]
…

Начинай ПРЯМО с `## 2.`, без преамбулы."""


PROMPT_STYLE = """Тебе дан корпус из 15 самых длинных сырых монологов Анны — это куски её непрерывной речи на лекциях, по 1500-25000 знаков каждый.

Задача: описать СТИЛЬ её речи структурно, чтобы LLM-генератор мог копировать стиль.

Включи разделы:
- **Регистр** (живой / академичный / смешанный; примеры из её речи)
- **Длина фраз** (короткие / периоды; типичные конструкции)
- **Обращение к слушателю** (ты / вы / по имени)
- **Юмор** (тип, степень провокативности; конкретные приёмы)
- **Ругательства и просторечия** (что использует, в каких контекстах)
- **Профессиональные термины** (как вводит, как поясняет)
- **Структурность** (любит ли нумерованные схемы, длинные периоды)

Каждый пункт обоснуй прямой цитатой из материала.
Если не уверен — `[нужно подтвердить с Анной]`.

Терминология: «супружество», не «брак».

Верни ТОЛЬКО markdown:

## 3. Стилевая характеристика

**Регистр.** [описание + цитата]

**Длина фраз.** [описание + цитата]

…

Начинай ПРЯМО с `## 3.`."""


PROMPT_SIGNATURE_PHRASES = """Тебе дан список question + metaphor концептов Анны — её фирменные вопросы и метафоры из 68 лекций.

Задача: выделить 8-12 САМЫХ узнаваемых формулировок, которые делают речь Анны её речью.

Правила:
- Это должны быть конкретные фразы / вопросы / метафоры, не пересказы
- Каждая формулировка — 1-2 предложения объяснения: что это значит, когда применяется
- Приоритет — те, что упоминаются ≥3 раз в концептах (это в их description обычно видно)
- Если не уверен — `[нужно подтвердить с Анной]`

Терминология: «супружество», не «брак».

Верни ТОЛЬКО markdown:

## 4. Фирменные формулировки

1. **«[цитата как формулировка]»** — [что значит, когда применяется]
2. **«[цитата]»** — [объяснение]
…

Начинай ПРЯМО с `## 4.`."""


PROMPT_TECHNIQUES = """Тебе дан список technique + exercise концептов Анны — приёмы и упражнения её метода.

Задача: сделать обзор её инструментария: 8-12 техник, каждая — 1 предложение «что это».

Правила:
- Это «что у меня в наборе», не пошаговое описание
- Не повторяй одно и то же разными словами
- Сгруппируй смыслово (слушание, конфликт, эмоции, паузы и т.д.)
- Если не уверен — `[нужно подтвердить с Анной]`

Терминология: «супружество», не «брак».

Верни ТОЛЬКО markdown:

## 5. Подходы и техники

1. **[название техники].** [одно предложение что это]
2. …

Начинай ПРЯМО с `## 5.`."""


PROMPT_RECOMMENDATIONS = """Тебе дан список recommendation-концептов Анны — книги, авторы, ресурсы, которые она упоминает в 68 лекциях.

Задача: оформить как список «что она рекомендует».

Правила:
- Книги — с автором (если назван)
- Группы ресурсов (дебаты, актёрские студии) — отдельным пунктом
- Если в концепте есть `[нужно подтвердить]` или только предположение — отметь так же
- Не выдумывай авторов / названия — если только имя упомянуто, оставь так

Терминология: «супружество», не «брак».

Верни ТОЛЬКО markdown:

## 6. Рекомендуемые источники

- **[название]** — [контекст или цитата, в каком случае рекомендует]
- …

Начинай ПРЯМО с `## 6.`."""


# Раздел 7 — статичный, не генерируется LLM
SECTION_7_STATIC = """## 7. Что нужно дополнить из интервью с Анной

1. **Точные границы метода.** С какими запросами / состояниями ты не берёшься? Что отказываешь?
2. **Что делаешь, если клиент в кризисе.** Маршрутизация: куда передаёшь, как объясняешь клиенту.
3. **Терапевтический альянс на первой встрече.** Как описываешь свою роль, формат, ожидания, ответственность.
4. **Граница «коуч / психолог / медиатор».** В лекциях используешь все три рамки — где проводишь грань для клиента?
5. **Регистр в разной аудитории.** На лекциях свободный — так же ли на индивидуальной консультации, с парой, с детьми, в бизнес-медиации?
6. **Двойная роль разработчик-клиент** (этот проект). Какие у тебя red lines в этой конфигурации?
7. **«Закрытие» сессии.** По каким признакам ты понимаешь, что сессия закрыта и материал можно использовать?
8. **Успешный кейс.** По каким маркерам видишь, что работа сложилась — и какие бывают неудачи?
9. **Чему НЕ учишь других психологов.** Какие части метода считаешь несостоявшимися / непереносимыми?
10. **Личные источники и учителя.** Кого читаешь, у кого училась, на какие школы опираешься? (Нужно для правильного референсного фона.)
"""


SECTIONS = [
    ("principles", PROMPT_PRINCIPLES, ["claim"]),
    ("red_lines", PROMPT_RED_LINES, ["warning"]),
    ("style", PROMPT_STYLE, None),  # источник — raw_quotes, не concepts
    ("signature_phrases", PROMPT_SIGNATURE_PHRASES, ["question", "metaphor"]),
    ("techniques", PROMPT_TECHNIQUES, ["technique", "exercise"]),
    ("recommendations", PROMPT_RECOMMENDATIONS, ["recommendation"]),
]


# === DB / data loaders ===

def get_concepts_by_types(types: list[str]) -> list[dict]:
    placeholders = ",".join(["%s"] * len(types))
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT name, type, description,
                   COALESCE(array_length(source_segments, 1), 0) AS mentions
            FROM concepts
            WHERE type IN ({placeholders})
              AND description IS NOT NULL AND length(description) > 20
            ORDER BY mentions DESC, name
            """,
            tuple(types),
        )
        return [
            {"name": r[0], "type": r[1], "description": r[2], "mentions": r[3]}
            for r in cur.fetchall()
        ]


def load_raw_quotes() -> list[dict]:
    if not RAW_QUOTES_PATH.exists():
        raise FileNotFoundError(
            f"{RAW_QUOTES_PATH} не найден. Запусти scripts/build_style_artifacts.py сначала."
        )
    return [json.loads(line) for line in RAW_QUOTES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def format_concepts_input(concepts: list[dict]) -> str:
    lines = []
    for c in concepts:
        m = f" ({c['mentions']} упом.)" if c["mentions"] else ""
        lines.append(f"- **{c['name']}** ({c['type']}){m} — {c['description']}")
    return "\n".join(lines)


def format_quotes_input(quotes: list[dict]) -> str:
    parts = []
    for i, q in enumerate(quotes, 1):
        parts.append(f"### Цитата {i} (из «{q.get('lecture', '?')}», {len(q['text'])} знаков)\n\n{q['text']}\n")
    return "\n".join(parts)


def build_section_request(section_key: str, prompt: str, types: list[str] | None) -> dict:
    """Один request для Batch API на один раздел."""
    if types is None:
        # style: используем raw_quotes
        quotes = load_raw_quotes()
        input_text = format_quotes_input(quotes)
    else:
        concepts = get_concepts_by_types(types)
        print(f"  [{section_key}] {len(concepts)} concepts of types {types}", flush=True)
        input_text = format_concepts_input(concepts)

    return {
        "custom_id": f"voice_doc__{section_key}",
        "params": {
            "model": MODEL,
            "max_tokens": 4000,
            "system": [
                {
                    "type": "text",
                    "text": prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [
                {"role": "user", "content": f"Материал для раздела:\n\n{input_text}"}
            ],
        },
    }


# === State management ===

def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(s: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


# === Batch ops ===

def submit_voice_doc_batch(client: Anthropic) -> str:
    print("Building 6 section requests…", flush=True)
    requests = [
        build_section_request(key, prompt, types) for key, prompt, types in SECTIONS
    ]
    print(f"Submitting batch ({len(requests)} requests)…", flush=True)
    batch = client.messages.batches.create(requests=requests)
    print(f"  → batch_id: {batch.id}", flush=True)
    return batch.id


def poll_batch(client: Anthropic, batch_id: str) -> None:
    while True:
        b = client.messages.batches.retrieve(batch_id)
        c = b.request_counts
        print(
            f"  [voice-doc] status={b.processing_status} "
            f"processing={c.processing} succeeded={c.succeeded} "
            f"errored={c.errored}",
            flush=True,
        )
        if b.processing_status == "ended":
            return
        time.sleep(POLL_INTERVAL_S)


def pull_and_merge(client: Anthropic, batch_id: str) -> dict[str, str]:
    """Pull results, возвращает dict section_key → markdown."""
    sections: dict[str, str] = {}
    errors: dict[str, str] = {}
    for result in client.messages.batches.results(batch_id):
        _, section_key = result.custom_id.rsplit("__", 1)
        if result.result.type != "succeeded":
            errors[section_key] = f"API error: {result.result.type}"
            continue
        text = result.result.message.content[0].text.strip()
        sections[section_key] = text
    if errors:
        print(f"WARNING: errors in sections: {errors}", flush=True)
    return sections


def merge_final_doc(sections: dict[str, str]) -> str:
    header = (
        "# Voice-document Анны (v2, черновик)\n\n"
        f"> Этот черновик сгенерирован автоматически из её 68 лекций "
        f"({datetime.utcnow().strftime('%Y-%m-%d')}). "
        f"Анна должна его прочитать и поправить.\n\n"
        f"> Каждое утверждение, помеченное `[нужно подтвердить с Анной]`, требует её прямого ответа.\n\n"
    )
    parts = [header]
    order = ["principles", "red_lines", "style", "signature_phrases", "techniques", "recommendations"]
    for key in order:
        if key in sections:
            parts.append(sections[key].strip())
            parts.append("\n")
        else:
            parts.append(f"## ❌ Раздел `{key}` не сгенерирован — см. лог batch'а.\n")
    parts.append(SECTION_7_STATIC.strip())
    parts.append("\n")
    return "\n".join(parts)


# === Main ===

def main() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    client = Anthropic(max_retries=8)
    state = load_state()

    if "batch_id" not in state:
        batch_id = submit_voice_doc_batch(client)
        state["batch_id"] = batch_id
        state["submitted_at"] = datetime.utcnow().isoformat()
        save_state(state)
        print(f"\n✓ Submitted. State → {STATE_PATH}\n", flush=True)
    else:
        print(f"Resuming. batch_id={state['batch_id']}\n", flush=True)

    print(f"Polling every {POLL_INTERVAL_S}s…", flush=True)
    poll_batch(client, state["batch_id"])

    print("\nPulling results…", flush=True)
    sections = pull_and_merge(client, state["batch_id"])
    print(f"  Got {len(sections)}/6 sections", flush=True)

    print("Merging final markdown…", flush=True)
    final = merge_final_doc(sections)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(final, encoding="utf-8")
    print(f"  → {OUT_PATH} ({len(final)} chars)", flush=True)

    state["done"] = True
    state["completed_at"] = datetime.utcnow().isoformat()
    state["sections_count"] = len(sections)
    save_state(state)

    print("\n✓ Voice-doc v2 ready for human review.", flush=True)
    print(f"  Открой: {OUT_PATH}", flush=True)
    print(f"  Далее: docker compose run --rm app python scripts/ingest_voice_doc.py", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
