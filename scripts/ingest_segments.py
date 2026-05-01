"""Загрузить data/transcripts/<lecture>/segments.json в clean_segments.

Идемпотентно: пропускает raw_transcripts, у которых уже есть clean_segments.
Связь по совпадению source_file. text для каждого clean_segment склеивается
из whisper-сегментов raw_transcripts.content в указанном временном диапазоне.

Запуск:
    docker compose run --rm app python scripts/ingest_segments.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from psy_helper.db.connection import connect


def collect_text(raw_segments: list[dict], start_ts: float, end_ts: float) -> str:
    parts = []
    for s in raw_segments:
        seg_start = float(s.get("start", 0))
        seg_end = float(s.get("end", 0))
        if seg_end <= start_ts or seg_start >= end_ts:
            continue
        text = (s.get("text") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def ingest_one(cur, raw_id: str, source_file: str, segments_path: Path) -> int:
    cur.execute("SELECT 1 FROM clean_segments WHERE raw_id = %s LIMIT 1", (raw_id,))
    if cur.fetchone():
        return -1  # уже есть

    cur.execute("SELECT content FROM raw_transcripts WHERE id = %s", (raw_id,))
    raw = cur.fetchone()[0]
    raw_segments = raw.get("segments", [])

    blocks = json.loads(segments_path.read_text(encoding="utf-8"))
    inserted = 0
    for block in blocks:
        text = collect_text(
            raw_segments,
            float(block["start_ts"]),
            float(block["end_ts"]),
        )
        cur.execute(
            """
            INSERT INTO clean_segments
              (raw_id, start_ts, end_ts, title, summary, text, segment_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                raw_id,
                block["start_ts"],
                block["end_ts"],
                block.get("title"),
                block.get("summary"),
                text,
                block.get("segment_type"),
            ),
        )
        inserted += 1
    return inserted


def main() -> int:
    load_dotenv()

    transcripts_dir = Path("data/transcripts")
    candidates = []
    for raw_path in sorted(transcripts_dir.glob("*/raw.json")):
        seg_path = raw_path.parent / "segments.json"
        if seg_path.exists():
            candidates.append((raw_path, seg_path))

    if not candidates:
        print("Не нашёл segments.json. Сначала запусти scripts/segment_via_claude.py")
        return 1

    print(f"Найдено пар raw.json + segments.json: {len(candidates)}")

    total_inserted = 0
    skipped = 0
    with connect() as conn, conn.cursor() as cur:
        for raw_path, seg_path in candidates:
            cur.execute(
                "SELECT id FROM raw_transcripts WHERE source_file = %s",
                (str(raw_path),),
            )
            row = cur.fetchone()
            if not row:
                print(f"  [!] {raw_path.parent.name}: нет в raw_transcripts (запусти ingest_raw)", file=sys.stderr)
                continue
            raw_id = row[0]
            n = ingest_one(cur, raw_id, str(raw_path), seg_path)
            if n == -1:
                skipped += 1
                print(f"  [=] {raw_path.parent.name}: clean_segments уже есть")
            else:
                total_inserted += n
                print(f"  [+] {raw_path.parent.name}: {n} блоков")
        conn.commit()

    print(f"\nИтого: добавлено {total_inserted} clean_segments, пропущено {skipped} файлов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
