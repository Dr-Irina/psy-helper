"""Загрузить data/transcripts/<lecture>/concepts.json в таблицу concepts.

Маппинг: source_block_indices (1-based, как в segments.json) →
clean_segments.id (UUID). Если концепт с тем же (therapist_id, name)
уже есть — расширяем source_segments объединением и убираем дубли.

Запуск:
    docker compose run --rm app python scripts/ingest_concepts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from psy_helper.db.connection import connect
from psy_helper.taxonomy import VALID_TYPES


def fetch_segment_uuids(cur, raw_id: str) -> list[str]:
    cur.execute(
        """
        SELECT id FROM clean_segments
        WHERE raw_id = %s
        ORDER BY start_ts
        """,
        (raw_id,),
    )
    return [r[0] for r in cur.fetchall()]


def main() -> int:
    load_dotenv()

    candidates = []
    for raw_path in sorted(Path("data/transcripts").glob("*/raw.json")):
        c = raw_path.parent / "concepts.json"
        if c.exists():
            candidates.append((raw_path, c))

    if not candidates:
        print("Не нашёл concepts.json. Сначала запусти extract_concepts_via_claude.py")
        return 1

    print(f"Найдено concepts.json: {len(candidates)}")

    total_inserted = 0
    total_updated = 0
    skipped_invalid = 0

    with connect() as conn, conn.cursor() as cur:
        for raw_path, concepts_path in candidates:
            cur.execute(
                "SELECT id, therapist_id FROM raw_transcripts WHERE source_file = %s",
                (str(raw_path),),
            )
            row = cur.fetchone()
            if not row:
                print(f"  [!] {raw_path.parent.name}: нет в raw_transcripts", file=sys.stderr)
                continue
            raw_id, therapist_id = row[0], row[1]
            seg_uuids = fetch_segment_uuids(cur, raw_id)
            if not seg_uuids:
                print(f"  [!] {raw_path.parent.name}: нет clean_segments", file=sys.stderr)
                continue

            concepts = json.loads(concepts_path.read_text(encoding="utf-8"))
            inserted = 0
            updated = 0
            invalid = 0
            for c in concepts:
                if c.get("type") not in VALID_TYPES:
                    invalid += 1
                    continue
                src_idx = c.get("source_block_indices") or []
                src_uuids = []
                for i in src_idx:
                    if 1 <= i <= len(seg_uuids):
                        src_uuids.append(seg_uuids[i - 1])
                if not src_uuids:
                    invalid += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO concepts (therapist_id, name, type, description, source_segments)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (therapist_id, name) DO UPDATE
                    SET source_segments = (
                      SELECT ARRAY(
                        SELECT DISTINCT unnest(
                          concepts.source_segments || EXCLUDED.source_segments
                        )
                      )
                    )
                    RETURNING (xmax = 0) AS inserted
                    """,
                    (therapist_id, c["name"], c["type"], c.get("description"), src_uuids),
                )
                was_inserted = cur.fetchone()[0]
                if was_inserted:
                    inserted += 1
                else:
                    updated += 1

            total_inserted += inserted
            total_updated += updated
            skipped_invalid += invalid
            print(
                f"  [+] {raw_path.parent.name}: inserted={inserted}, "
                f"merged={updated}, invalid={invalid}"
            )
        conn.commit()

    print(f"\nИтого: inserted={total_inserted}, merged={total_updated}, invalid={skipped_invalid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
