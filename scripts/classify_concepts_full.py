"""Полная классификация концептов: тематизация + ступени Ханта через Batch API.

Submit'ит два батча (topics, hunt_stages) на ВСЕ концепты с непустым описанием.
Polling до завершения (1-3ч обычно), UPDATE concepts в БД.

Запуск (в фоне, чтобы не блокировать терминал):
    docker compose run -d --name psy-classify app python scripts/classify_concepts_full.py
    docker logs -f psy-classify     # смотреть прогресс

Идемпотентно: state в data/classify_state.json. При перезапуске продолжит
с того же batch_id (не отправляя заново — экономия).

Reset (если хочешь начать заново):
    rm data/classify_state.json
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

MODEL = "claude-haiku-4-5"
STATE_PATH = Path("data/classify_state.json")
POLL_INTERVAL_S = 60


# Промты дублируются с scripts/classify_concepts_sample.py.
# TODO: вынести в psy_helper/classify/prompts.py когда дойдём до v0 content engine.

TOPICS_SYSTEM = """Ты — классификатор концептов психолога Анны по тематическим тегам.

Каждый концепт может относиться к ОДНОМУ ИЛИ НЕСКОЛЬКИМ топикам из списка:

- marriage — Супружество (отношения супругов, конфликты в паре, измены, развод, ревность, тёщи/свекрови)
- partnership — Партнёрство (деловое, рабочее: совместный бизнес, разделение зон ответственности, допартнёрские соглашения)
- children — Дети (родительство, воспитание дошкольников и школьников, общение с детьми)
- teens — Подростки (подростковый бунт, сепарация, общение с подростками)
- confidence — Уверенность (психологический «взрослый», свобода говорить, самооценка, отстаивание границ)
- personal_effectiveness — Личная эффективность (цели, действие, дисциплина, выгорание, продуктивность)
- finance — Финансы (деньги в отношениях, экономика семьи, продажа экспертизы)
- communication — Коммуникация (универсальные техники общения, переносятся в любую сферу)
- general — Общее (фундаментальные принципы метода, не привязанные к одной теме)

Большинство концептов получит несколько тегов. Примеры:
- «Я-высказывание» (technique) → topics: ["communication","marriage","general"], subtopics: ["я-высказывание"]
- «Допартнёрское соглашение в бизнесе» (technique) → topics: ["partnership","finance"], subtopics: ["договоры","ответственность"]
- «Подростковый бунт это норма» (claim) → topics: ["teens","children"], subtopics: ["сепарация"]
- «Эмпатия ментальная vs эмоциональная» (term) → topics: ["communication","general","marriage","children"], subtopics: ["эмпатия"]
- «Книга Психологическое айкидо» (recommendation) → topics: ["communication","general"], subtopics: ["обезоруживание","книги"]

Также придумай 1-3 узких subtopic-тега на русском, описывающих более конкретную тему концепта.

Верни СТРОГО JSON, без обёртки markdown:
{
  "topics": ["..."],
  "subtopics": ["..."],
  "confidence": {"marriage": 0.9, "communication": 0.7}
}

confidence — для каждого выбранного topic от 0 до 1. Только JSON, ничего больше."""


HUNT_STAGES_SYSTEM = """Ты — классификатор концептов психолога Анны по их потенциалу для разных ступеней лестницы Ханта.

Лестница Ханта — 5 ступеней готовности клиента покупать:

1. Безразличие — клиент не осознаёт проблему. Контент: провокационный, цепляет «А ты замечаешь?»
2. Осведомлённость — проблема видна, не знает решения. Контент: образовательный, «3 признака что у вас ссора, а не конфликт»
3. Сравнение — клиент ищет решение, сравнивает подходы. Контент: позиционирующий, «почему КПТ-горизонт vs классический психоанализ»
4. Выбор — клиент выбрал подход, выбирает эксперта. Контент: про специалиста лично, кейсы, ценности, голос
5. Покупка — клиент готов платить. Контент: оффер, цена, демо, личное предложение

ВАЖНО: концепт НЕ «принадлежит» одной ступени. Один концепт можно подать на любой ступени, меняется только упаковка.

Например, «эмпатия»:
- 1: «Сколько раз ты сегодня по-настоящему слушала партнёра?»
- 2: «Эмпатия — не "поддакивать", это слышать и проверять услышанное»
- 3: «Мы делаем 3-шаговую структуру эмпатии за полгода»
- 4: «Мои клиенты доходят до устойчивой эмпатии за полгода-год»
- 5: «На курсе — 3 модуля упражнений на эмпатию»

Универсальный концепт получит [1,2,3,4,5]. Узкие концепты — меньше:
- warning «не предлагай партнёру использовать GPT как арбитра» → [2,3] (осознание проблемы, сравнение подходов)
- term «КПТ-горизонт» → [3,4] (позиционирование метода, выбор специалиста)
- recommendation «книга "Психологическое айкидо"» → [2,3] (формирование, сравнение)
- claim «единственный показатель понимания — действие» → [1,2,3,4,5] (универсальный принцип)

Верни СТРОГО JSON, без обёртки markdown:
{
  "hunt_stages": [1,2,3],
  "confidence": {"1": 0.8, "2": 0.9, "3": 0.7}
}

Только JSON, ничего больше."""


# === State management ===

def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(s: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


# === DB ===

def get_all_concepts() -> list[dict]:
    """Все концепты с непустым описанием."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, name, type, description
            FROM concepts
            WHERE description IS NOT NULL AND length(description) > 20
            ORDER BY id
            """
        )
        return [
            {"id": r[0], "name": r[1], "type": r[2], "description": r[3]}
            for r in cur.fetchall()
        ]


# === Batch construction ===

def user_message(concept: dict) -> str:
    return (
        f'Концепт: "{concept["name"]}"\n'
        f'Тип: {concept["type"]}\n'
        f'Описание: {concept["description"]}'
    )


def build_requests(
    concepts: list[dict], system_prompt: str, task_name: str, max_tokens: int
) -> list[dict]:
    return [
        {
            "custom_id": f"{c['id']}__{task_name}",
            "params": {
                "model": MODEL,
                "max_tokens": max_tokens,
                "system": [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "messages": [{"role": "user", "content": user_message(c)}],
            },
        }
        for c in concepts
    ]


def submit_batch(client: Anthropic, requests: list[dict], label: str) -> str:
    print(f"  Submitting {label} batch: {len(requests)} requests…", flush=True)
    batch = client.messages.batches.create(requests=requests)
    print(f"  → batch_id: {batch.id}", flush=True)
    return batch.id


# === Polling ===

def poll_batch(client: Anthropic, batch_id: str, label: str) -> None:
    """Polling до тех пор пока статус != 'in_progress'."""
    while True:
        b = client.messages.batches.retrieve(batch_id)
        c = b.request_counts
        print(
            f"  [{label}] status={b.processing_status} "
            f"processing={c.processing} succeeded={c.succeeded} "
            f"errored={c.errored} canceled={c.canceled} expired={c.expired}",
            flush=True,
        )
        if b.processing_status == "ended":
            return
        time.sleep(POLL_INTERVAL_S)


# === Result ingest ===

def parse_response(text: str) -> dict | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def parse_custom_id(custom_id: str) -> tuple[str, str]:
    cid, task = custom_id.rsplit("__", 1)
    return cid, task


def ingest_results(client: Anthropic, batch_id: str, task_name: str) -> dict:
    """Streaming pull результатов + UPDATE concepts. Возвращает статистику."""
    stats = {"ok": 0, "parse_error": 0, "api_error": 0}

    with connect() as conn:
        with conn.cursor() as cur:
            for result in client.messages.batches.results(batch_id):
                concept_id, _ = parse_custom_id(result.custom_id)

                if result.result.type != "succeeded":
                    stats["api_error"] += 1
                    continue

                text = result.result.message.content[0].text
                parsed = parse_response(text)
                if parsed is None:
                    stats["parse_error"] += 1
                    continue

                if task_name == "topics":
                    cur.execute(
                        """
                        UPDATE concepts
                           SET topics = %s, subtopics = %s
                         WHERE id = %s
                        """,
                        (
                            parsed.get("topics", []),
                            parsed.get("subtopics", []),
                            concept_id,
                        ),
                    )
                elif task_name == "stages":
                    cur.execute(
                        "UPDATE concepts SET hunt_stages = %s WHERE id = %s",
                        (parsed.get("hunt_stages", []), concept_id),
                    )
                stats["ok"] += 1
        conn.commit()
    return stats


# === Main ===

def main() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    client = Anthropic(max_retries=8)
    state = load_state()

    # 1. Submit batches if not yet
    if "topics_batch_id" not in state:
        print("Loading concepts from DB…", flush=True)
        concepts = get_all_concepts()
        print(f"Got {len(concepts)} concepts.\n", flush=True)

        topics_reqs = build_requests(concepts, TOPICS_SYSTEM, "topics", 300)
        stages_reqs = build_requests(concepts, HUNT_STAGES_SYSTEM, "stages", 200)

        print(f"Submitting batches (total requests: {len(topics_reqs) + len(stages_reqs)})…", flush=True)
        state["topics_batch_id"] = submit_batch(client, topics_reqs, "topics")
        state["stages_batch_id"] = submit_batch(client, stages_reqs, "stages")
        state["submitted_at"] = datetime.utcnow().isoformat()
        state["total_concepts"] = len(concepts)
        save_state(state)
        print(f"\n✓ Submitted. State saved to {STATE_PATH}\n", flush=True)
    else:
        print(f"Resuming from state file: {STATE_PATH}", flush=True)
        print(f"  topics_batch_id: {state['topics_batch_id']}", flush=True)
        print(f"  stages_batch_id: {state['stages_batch_id']}\n", flush=True)

    # 2. Poll until both ended
    print(f"Polling batches every {POLL_INTERVAL_S}s (batches processed in parallel by Anthropic)…", flush=True)
    if not state.get("topics_done"):
        poll_batch(client, state["topics_batch_id"], "topics")
    if not state.get("stages_done"):
        poll_batch(client, state["stages_batch_id"], "stages")

    # 3. Ingest results
    if not state.get("topics_done"):
        print("\nIngesting topics results…", flush=True)
        topics_stats = ingest_results(client, state["topics_batch_id"], "topics")
        print(f"  topics: {topics_stats}", flush=True)
        state["topics_done"] = True
        state["topics_stats"] = topics_stats
        save_state(state)

    if not state.get("stages_done"):
        print("\nIngesting hunt_stages results…", flush=True)
        stages_stats = ingest_results(client, state["stages_batch_id"], "stages")
        print(f"  hunt_stages: {stages_stats}", flush=True)
        state["stages_done"] = True
        state["stages_stats"] = stages_stats
        save_state(state)

    print("\n✓ All done.", flush=True)
    print(f"  Topics: {state.get('topics_stats', {})}", flush=True)
    print(f"  Hunt stages: {state.get('stages_stats', {})}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
