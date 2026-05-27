"""Собирает артефакты стиля Анны для content engine:

- data/style/raw_quotes.jsonl — топ-15 длинных монологов Анны (≥1500 знаков,
    подряд идущие сегменты доминантного спикера без перебивок).
- data/style/lexicon.json — фирменные question + metaphor концепты,
    отсортированные по частоте упоминаний.
- data/style/forbidden_topics.json — стартовый стоп-лист тем для генератора.

Запуск:
    docker compose run --rm app python scripts/build_style_artifacts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from psy_helper.db.connection import connect

load_dotenv()

OUT_DIR = Path("data/style")
QUOTES_PATH = OUT_DIR / "raw_quotes.jsonl"
LEXICON_PATH = OUT_DIR / "lexicon.json"
FORBIDDEN_PATH = OUT_DIR / "forbidden_topics.json"

MIN_MONOLOGUE_LEN = 1500
TOP_N_QUOTES = 15


# === Style corpus ===

def dominant_speaker(segments: list) -> str | None:
    """Спикер с максимальной суммарной длительностью реплик."""
    durations: dict[str, float] = {}
    for s in segments:
        sp = s.get("speaker")
        if not sp:
            continue
        durations[sp] = durations.get(sp, 0.0) + float(s.get("end", 0)) - float(s.get("start", 0))
    if not durations:
        return None
    return max(durations.items(), key=lambda kv: kv[1])[0]


def extract_monologues(segments: list, speaker: str) -> list[dict]:
    """Подряд идущие сегменты одного спикера → склеенные монологи."""
    blocks: list[dict] = []
    current: list[dict] = []

    def flush():
        if not current:
            return
        text = " ".join((s.get("text") or "").strip() for s in current).strip()
        if text:
            blocks.append({
                "text": text,
                "start_ts": current[0].get("start"),
                "end_ts": current[-1].get("end"),
                "speaker": speaker,
            })

    for s in segments:
        if s.get("speaker") == speaker:
            current.append(s)
        else:
            flush()
            current = []
    flush()
    return blocks


def build_style_corpus() -> int:
    """Возвращает количество сохранённых цитат."""
    all_blocks: list[dict] = []
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT rt.id::text, rt.source_file, rt.content
            FROM raw_transcripts rt
            JOIN therapists t ON t.id = rt.therapist_id
            WHERE t.name = 'Анна'
            """
        )
        for raw_id, source_file, content in cur.fetchall():
            segments = content.get("segments", []) if isinstance(content, dict) else []
            dom = dominant_speaker(segments)
            if not dom:
                continue
            for m in extract_monologues(segments, dom):
                if len(m["text"]) >= MIN_MONOLOGUE_LEN:
                    m["raw_id"] = raw_id
                    parts = source_file.split("/")
                    m["lecture"] = parts[-2] if len(parts) >= 2 else source_file
                    all_blocks.append(m)

    all_blocks.sort(key=lambda b: len(b["text"]), reverse=True)
    top = all_blocks[:TOP_N_QUOTES]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with QUOTES_PATH.open("w", encoding="utf-8") as f:
        for b in top:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")

    print(f"\n[Style corpus]")
    print(f"  Total monologues ≥{MIN_MONOLOGUE_LEN} chars: {len(all_blocks)}")
    print(f"  Saved top {len(top)} → {QUOTES_PATH}")
    for i, b in enumerate(top[:5], 1):
        preview = b["text"][:80].replace("\n", " ")
        print(f"  {i}. [{b['lecture']}] {len(b['text'])} chars · «{preview}…»")
    return len(top)


# === Lexicon ===

def build_lexicon() -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, type, description,
                   COALESCE(array_length(source_segments, 1), 0) AS mentions
            FROM concepts
            WHERE type IN ('question', 'metaphor')
              AND description IS NOT NULL
              AND length(description) > 10
            ORDER BY mentions DESC, name
            """
        )
        rows = cur.fetchall()

    lexicon: dict[str, list[dict]] = {"questions": [], "metaphors": []}
    for name, type_, desc, mentions in rows:
        item = {"phrase": name, "description": desc, "mentions": mentions}
        if type_ == "question":
            lexicon["questions"].append(item)
        elif type_ == "metaphor":
            lexicon["metaphors"].append(item)

    LEXICON_PATH.write_text(
        json.dumps(lexicon, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n[Lexicon]")
    print(f"  Saved {len(lexicon['questions'])} questions, {len(lexicon['metaphors'])} metaphors → {LEXICON_PATH}")
    print("  Топ-5 фирменных вопросов:")
    for q in lexicon["questions"][:5]:
        print(f"    • {q['phrase']} ({q['mentions']} упом.)")
    print("  Топ-5 метафор:")
    for m in lexicon["metaphors"][:5]:
        print(f"    • {m['phrase']} ({m['mentions']} упом.)")
    return lexicon


# === Forbidden topics (стартовый список) ===

FORBIDDEN_TOPICS = {
    "version": 1,
    "updated_at": "2026-05-27",
    "topics": [
        {
            "id": "diagnoses",
            "label": "Диагнозы и медицинские утверждения",
            "examples": ["депрессия", "тревожное расстройство", "ПТСР", "БАР", "СДВГ"],
            "reason": "Психолог не имеет права ставить диагнозы; AI тем более.",
        },
        {
            "id": "acute_states",
            "label": "Острые состояния и кризисы",
            "examples": ["суицид", "самоповреждение", "острая травма", "острый психоз"],
            "reason": "Безопасность: эти темы требуют живого специалиста, не AI-контента.",
        },
        {
            "id": "specific_clients",
            "label": "Конкретные клиенты Анны",
            "examples": ["имя клиента", "узнаваемое описание", "детали кейса"],
            "reason": "Конфиденциальность.",
        },
        {
            "id": "medical_advice",
            "label": "Медицинские советы",
            "examples": ["препараты", "дозировки", "взаимодействия лекарств", "лечение"],
            "reason": "Вне компетенции психолога.",
        },
        {
            "id": "guarantees",
            "label": "Гарантии результата терапии",
            "examples": ["100% результат", "вылечу за месяц", "точно поможет"],
            "reason": "Этика: терапия не гарантирует результата.",
        },
        {
            "id": "third_party_analysis",
            "label": "Анализ чужих отношений по описанию",
            "examples": ["разбор переписки клиента с партнёром", "оценка отсутствующих людей"],
            "reason": "Метод Анны прямо против этого — см. её предостережение про GPT-арбитра.",
        },
    ],
}


def build_forbidden() -> None:
    FORBIDDEN_PATH.write_text(
        json.dumps(FORBIDDEN_TOPICS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[Forbidden topics]")
    print(f"  Saved {len(FORBIDDEN_TOPICS['topics'])} стартовых стоп-тем → {FORBIDDEN_PATH}")
    print(f"  (Анна сможет расширить или скорректировать этот список после ревью.)")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_style_corpus()
    build_lexicon()
    build_forbidden()
    print("\n✓ Style artifacts ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
