"""Классификация концептов локальной моделью (Ollama): topics/subtopics + hunt_stages.

Перезалив v2 обнулил классификацию (она была на старом корпусе). Здесь — заново,
но локально и бесплатно (gemma2:27b), а не через Anthropic Batch. Классификация в
фиксированную таксономию легче извлечения, локальной модели хватает.

Батчами (по N концептов на вызов), два прохода. Идемпотентно: пропускает уже
размеченные (topics/hunt_stages IS NOT NULL). Сэмпл: --limit 30.

Запуск (на ХОСТЕ — Ollama на localhost; postgres через проброшенный порт):
    POSTGRES_HOST=localhost python3 scripts/classify_concepts_local.py --limit 30   # сэмпл
    POSTGRES_HOST=localhost python3 scripts/classify_concepts_local.py              # все
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from dotenv import load_dotenv

from psy_helper.db.connection import connect

API_URL = os.getenv("CLASSIFY_API_URL", "http://localhost:11434/v1/chat/completions")
MODEL = os.getenv("CLASSIFY_MODEL", "gemma2:27b")
BATCH = 12

TOPICS_RUBRIC = """Темы (ОДНА ИЛИ НЕСКОЛЬКО на концепт):
- marriage — супружество (отношения супругов, конфликты в паре, измены, развод, ревность, тёщи/свекрови)
- partnership — деловое партнёрство (бизнес, зоны ответственности, договоры)
- children — дети (родительство, воспитание дошкольников/школьников)
- teens — подростки (бунт, сепарация)
- confidence — уверенность (взрослая позиция, самооценка, границы)
- personal_effectiveness — личная эффективность (цели, дисциплина, выгорание, продуктивность)
- finance — финансы (деньги в отношениях, экономика семьи, продажа экспертизы)
- communication — коммуникация (универсальные техники общения)
- general — общее (фундаментальные принципы метода)
Плюс 1-3 узких subtopic-тега на русском."""

HUNT_RUBRIC = """Ступени Ханта (на каких концепт можно подать; концепт НЕ принадлежит одной):
1 безразличие (провокация «а ты замечаешь?») | 2 осведомлённость (образовательный) |
3 сравнение (позиционирование подхода) | 4 выбор (про эксперта, кейсы, голос) | 5 покупка (оффер).
Универсальный принцип → [1,2,3,4,5]; узкий → меньше (напр. [2,3])."""


def _call(system: str, user: str, timeout: int = 600) -> dict:
    body = {"model": MODEL, "temperature": 0,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_object"}, "stream": False}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer ollama"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        content = json.loads(r.read().decode())["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.DOTALL)
    return json.loads(m.group(0) if m else content)


def _render(concepts: list[dict]) -> str:
    return "\n".join(f'{i+1}. [{c["type"]}] {c["name"]} — {c["description"]}'
                     for i, c in enumerate(concepts))


def classify_batch(concepts: list[dict]) -> list[dict]:
    sys_t = ("Ты классификатор концептов психолога Анны.\n" + TOPICS_RUBRIC + "\n\n" + HUNT_RUBRIC +
             "\n\nНЕ скупись: типичный концепт получает 2-3 темы (техники общения и принципы "
             "переносятся в несколько сфер). По Ханту: широкий принцип/техника → много ступеней "
             "(часто [1,2,3,4,5]); узкий факт → 2-3 ступени. Почти не ставь только одну ступень для "
             "общих идей."
             '\n\nВерни СТРОГО JSON: {"results":[{"i":1,"topics":["..."],"subtopics":["..."],"hunt_stages":[1,2]}, ...]}'
             " по одному объекту на каждый концепт, i = его номер.")
    user = "Классифицируй каждый:\n" + _render(concepts)
    res = _call(sys_t, user)
    return res.get("results", []) if isinstance(res, dict) else []


VALID_TOPICS = {"marriage", "partnership", "children", "teens", "confidence",
                "personal_effectiveness", "finance", "communication", "general"}


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="сэмпл (0 = все неразмеченные)")
    args = ap.parse_args()

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, name, type, description FROM concepts
            WHERE description IS NOT NULL AND length(description) > 15
              AND (topics IS NULL OR hunt_stages IS NULL)
            ORDER BY id
            """ + (f" LIMIT {int(args.limit)}" if args.limit else "")
        )
        rows = [{"id": r[0], "name": r[1], "type": r[2], "description": r[3]} for r in cur.fetchall()]

    print(f"Модель: {MODEL} | к разметке: {len(rows)} концептов | батч {BATCH}")
    done = 0
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        try:
            results = classify_batch(batch)
        except Exception as e:
            print(f"  [!] батч {start}: {type(e).__name__}: {e}")
            continue
        by_i = {int(r.get("i", 0)): r for r in results if isinstance(r, dict)}
        with connect() as conn, conn.cursor() as cur:
            for i, c in enumerate(batch, 1):
                r = by_i.get(i)
                if not r:
                    continue
                topics = [t for t in (r.get("topics") or []) if t in VALID_TOPICS]
                subs = [s for s in (r.get("subtopics") or []) if isinstance(s, str)][:3]
                hunt = [h for h in (r.get("hunt_stages") or []) if isinstance(h, int) and 1 <= h <= 5]
                cur.execute(
                    "UPDATE concepts SET topics=%s, subtopics=%s, hunt_stages=%s WHERE id=%s",
                    (topics or None, subs or None, hunt or None, c["id"]),
                )
                done += 1
            conn.commit()
        print(f"  размечено {done}/{len(rows)}")
    print(f"Готово: {done} концептов классифицировано.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
