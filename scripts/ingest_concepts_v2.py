"""Re-ingest перевыделенных концептов (concepts_v2.json) в таблицу concepts.

Отличия от ingest_concepts.py:
  - Читает concepts_v2.json (новая структура: + quotes + salience).
  - Чистый перезалив: бэкап старых концептов → TRUNCATE → загрузка заново
    (а не нарастание поверх старого корпуса). Требует миграцию 007.
  - quotes пишутся в JSONB как [{ "text": "...", "speaker": "<label Ани>" }],
    speaker берётся из data/speakers.json.
  - ON CONFLICT (therapist_id, name): одинаковые по имени концепты из разных
    лекций сливаются — quotes объединяются (накопление цитат), source_segments
    объединяются, salience = максимум. Семантические дубли с РАЗНЫМИ именами
    объединяет потом scripts/consolidate_concepts.py.

Запуск (в docker, после миграции 007 и extract_concepts_local.py):
    docker compose run --rm app python scripts/ingest_concepts_v2.py
    # только пилотные лекции:
    docker compose run --rm app python scripts/ingest_concepts_v2.py "Кофе с психологом. Тревожность"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from psycopg.types.json import Json

from psy_helper.content_gen.pii import detect_pii
from psy_helper.db.connection import connect
from psy_helper.taxonomy import VALID_TYPES

TRANSCRIPTS = Path("data/transcripts")
SPEAKERS_PATH = Path("data/speakers.json")
BACKUP_TABLE = "concepts_v1_backup"
PII_REPORT = Path("data/pii_review.md")


def fetch_segment_uuids(cur, raw_id: str) -> list[str]:
    cur.execute(
        "SELECT id FROM clean_segments WHERE raw_id = %s ORDER BY start_ts",
        (raw_id,),
    )
    return [r[0] for r in cur.fetchall()]


def backup_and_truncate(cur, lectures_filter: list[str] | None) -> None:
    """Бэкап старого корпуса в concepts_v1_backup и очистка concepts.

    При пилоте (заданы конкретные лекции) НЕ truncate'им весь корпус — удаляем
    только концепты этих лекций (по raw_id через source_segments слишком сложно),
    поэтому для пилота просто грузим поверх с ON CONFLICT (без полной очистки)."""
    cur.execute("SELECT count(*) FROM concepts")
    n = cur.fetchone()[0]
    if lectures_filter:
        print(f"Пилот-режим: полная очистка НЕ выполняется (концептов сейчас: {n}).")
        return
    if n:
        cur.execute(f"DROP TABLE IF EXISTS {BACKUP_TABLE}")
        cur.execute(f"CREATE TABLE {BACKUP_TABLE} AS TABLE concepts")
        print(f"Бэкап {n} концептов → {BACKUP_TABLE}")
    cur.execute("TRUNCATE concepts")
    print("concepts очищена для чистого перезалива.")


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("lectures", nargs="*", help="имена папок (по умолчанию все с concepts_v2.json)")
    args = ap.parse_args()

    speakers = json.loads(SPEAKERS_PATH.read_text(encoding="utf-8")) if SPEAKERS_PATH.exists() else {}

    if args.lectures:
        dirs = [TRANSCRIPTS / n for n in args.lectures]
    else:
        dirs = sorted(p.parent for p in TRANSCRIPTS.glob("*/concepts_v2.json"))
    if not dirs:
        print("Не нашёл concepts_v2.json — сначала extract_concepts_local.py")
        return 1

    total_ins = total_merged = total_invalid = 0
    pii_hits: list[tuple[str, str, str, list[str]]] = []  # (лекция, концепт, цитата, флаги)
    with connect() as conn, conn.cursor() as cur:
        backup_and_truncate(cur, args.lectures or None)

        for d in dirs:
            cpath = d / "concepts_v2.json"
            if not cpath.exists():
                continue
            cur.execute("SELECT id, therapist_id FROM raw_transcripts WHERE source_file = %s",
                        (str(d / "raw.json"),))
            row = cur.fetchone()
            if not row:
                print(f"  [!] {d.name}: нет в raw_transcripts", file=sys.stderr)
                continue
            raw_id, therapist_id = row
            seg_uuids = fetch_segment_uuids(cur, raw_id)
            if not seg_uuids:
                print(f"  [!] {d.name}: нет clean_segments", file=sys.stderr)
                continue
            anna = (speakers.get(d.name) or {}).get("anna")

            concepts = json.loads(cpath.read_text(encoding="utf-8"))
            ins = merged = invalid = 0
            for c in concepts:
                if c.get("type") not in VALID_TYPES:
                    invalid += 1
                    continue
                src_uuids = [seg_uuids[i - 1] for i in (c.get("source_block_indices") or [])
                             if isinstance(i, int) and 1 <= i <= len(seg_uuids)]
                quotes = []
                for q in (c.get("quotes") or []):
                    if not q:
                        continue
                    flags = detect_pii(q)  # имена/телефоны/email — флаг, НЕ блок
                    quotes.append({"text": q, "speaker": anna, "pii": flags or None})
                    if flags:
                        pii_hits.append((d.name, c.get("name", ""), q, flags))
                if not quotes:
                    invalid += 1
                    continue
                sal = c.get("salience") if isinstance(c.get("salience"), int) else 2
                cur.execute(
                    """
                    INSERT INTO concepts (therapist_id, name, type, description,
                                          source_segments, quotes, salience)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (therapist_id, name) DO UPDATE SET
                      source_segments = ARRAY(SELECT DISTINCT unnest(
                          concepts.source_segments || EXCLUDED.source_segments)),
                      quotes   = COALESCE(concepts.quotes, '[]'::jsonb) || EXCLUDED.quotes,
                      salience = GREATEST(COALESCE(concepts.salience, 0), EXCLUDED.salience)
                    RETURNING (xmax = 0) AS inserted
                    """,
                    (therapist_id, c["name"], c["type"], c.get("description"),
                     src_uuids, Json(quotes), sal),
                )
                if cur.fetchone()[0]:
                    ins += 1
                else:
                    merged += 1
            total_ins += ins; total_merged += merged; total_invalid += invalid
            print(f"  [+] {d.name}: inserted={ins}, merged={merged}, invalid={invalid}")
        conn.commit()

    print(f"\nИтого: inserted={total_ins}, merged={total_merged}, invalid={total_invalid}")

    if pii_hits:
        lines = ["# PII в цитатах — на ревью Ани\n",
                 "Флаги (имена/телефоны/email) в дословных цитатах. Это НЕ блок — проверь,",
                 "нет ли реальных клиентов; при необходимости убери/обезличь цитату.\n"]
        for lec, concept, quote, flags in pii_hits:
            lines.append(f"- **{lec}** · _{concept}_ · {', '.join(flags)}\n  - 🗣 «{quote}»")
        PII_REPORT.write_text("\n".join(lines), encoding="utf-8")
        print(f"⚠ PII-флагов: {len(pii_hits)} → {PII_REPORT} (на ревью Ани, не блок).")
    else:
        print("PII-флагов в цитатах не найдено.")

    print("Дальше: embed_concepts.py (переэмбеддинг) → consolidate_concepts.py (семантич. дубли).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
