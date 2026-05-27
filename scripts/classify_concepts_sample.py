"""Sample-тест классификации концептов: тематизация + ступени Ханта.

Берёт 50 случайных концептов из БД, прогоняет два промта (topics, hunt_stages)
параллельно через Anthropic API (Haiku 4.5 + prompt caching), сохраняет
результаты в jsonl и печатает сводку для глазного review перед полным прогоном.

Запуск:
    docker compose run --rm app python scripts/classify_concepts_sample.py
Output:
    data/classify_samples/topics_sample.jsonl
    data/classify_samples/hunt_stages_sample.jsonl
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from psy_helper.db.connection import connect

load_dotenv()

MODEL = "claude-haiku-4-5"
SAMPLE_SIZE = 50
MAX_WORKERS = 3
OUT_DIR = Path("data/classify_samples")


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


def get_sample(n: int) -> list[dict]:
    """Случайные n концептов с непустым описанием."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, name, type, description
            FROM concepts
            WHERE description IS NOT NULL AND length(description) > 20
            ORDER BY random()
            LIMIT %s
            """,
            (n,),
        )
        return [
            {"id": r[0], "name": r[1], "type": r[2], "description": r[3]}
            for r in cur.fetchall()
        ]


def user_message(concept: dict) -> str:
    return (
        f'Концепт: "{concept["name"]}"\n'
        f'Тип: {concept["type"]}\n'
        f'Описание: {concept["description"]}'
    )


def classify(client: Anthropic, system_prompt: str, concept: dict, max_tokens: int) -> dict:
    """Один LLM-вызов на один концепт. Возвращает распарсенный JSON или ошибку."""
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message(concept)}],
        )
        raw = msg.content[0].text.strip()
        # На случай если модель всё же обернула в ```json
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"_error": "json_decode", "_raw": raw[:300], "_msg": str(e)}
    except Exception as e:
        return {"_error": type(e).__name__, "_msg": str(e)}


def run_parallel(client, sample, system_prompt, max_tokens, name) -> list[dict]:
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(classify, client, system_prompt, c, max_tokens): c for c in sample
        }
        for i, future in enumerate(as_completed(futures), 1):
            concept = futures[future]
            classification = future.result()
            results.append({**concept, "classification": classification})
            print(f"  {name}: {i}/{len(sample)}", end="\r")
    print()
    return results


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def print_summary(rows: list[dict], key: str, label: str) -> None:
    counter: Counter = Counter()
    errors = 0
    for r in rows:
        c = r.get("classification", {})
        if "_error" in c:
            errors += 1
            continue
        for tag in c.get(key, []):
            counter[tag] += 1
    print(f"\n{label} (errors: {errors}/{len(rows)}):")
    for tag, cnt in sorted(counter.items(), key=lambda kv: -kv[1]):
        bar = "█" * cnt
        print(f"  {tag:<28} {cnt:>3}  {bar}")


def print_sample_rows(rows: list[dict], key: str, n: int = 10) -> None:
    print(f"\nПримеры ({n} случайных):")
    import random

    for r in random.sample(rows, min(n, len(rows))):
        c = r.get("classification", {})
        if "_error" in c:
            print(f"  ❌ {r['name']} → {c.get('_error')}")
            continue
        tags = c.get(key, [])
        print(f"  • {r['name']} ({r['type']}) → {tags}")


def main() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set in environment.", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Sampling {SAMPLE_SIZE} random concepts from DB…")
    sample = get_sample(SAMPLE_SIZE)
    print(f"Got {len(sample)} concepts.\n")

    client = Anthropic(max_retries=8)

    print(f"[1/2] Topics classification (Haiku, {MAX_WORKERS} parallel)…")
    topics_results = run_parallel(client, sample, TOPICS_SYSTEM, 400, "topics")
    write_jsonl(OUT_DIR / "topics_sample.jsonl", topics_results)

    print(f"[2/2] Hunt stages classification (Haiku, {MAX_WORKERS} parallel)…")
    stages_results = run_parallel(client, sample, HUNT_STAGES_SYSTEM, 300, "stages")
    write_jsonl(OUT_DIR / "hunt_stages_sample.jsonl", stages_results)

    print_summary(topics_results, "topics", "Распределение топиков")
    print_sample_rows(topics_results, "topics", 12)

    print_summary(stages_results, "hunt_stages", "Распределение ступеней Ханта")
    print_sample_rows(stages_results, "hunt_stages", 12)

    print(f"\n✓ Готово.")
    print(f"  {OUT_DIR / 'topics_sample.jsonl'}")
    print(f"  {OUT_DIR / 'hunt_stages_sample.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
