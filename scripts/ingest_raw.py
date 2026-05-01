"""Загрузить raw.json лекций из data/transcripts/*/ в raw_transcripts.

Идемпотентно: считает sha256 файла, конфликт по (source_file, source_hash)
пропускается. Доминантный спикер (по сумме длительности) сохраняется в
metadata.therapist_speaker_id — для лекций это эвристика «кто говорит больше
всех = психолог».

Запуск:
    docker compose run --rm app python scripts/ingest_raw.py [--therapist-name "Имя"]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from psy_helper.db.connection import connect


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def dominant_speaker(segments: list[dict]) -> str | None:
    durations: dict[str, float] = {}
    for s in segments:
        sp = s.get("speaker")
        if not sp:
            continue
        durations[sp] = durations.get(sp, 0.0) + float(s.get("end", 0)) - float(s.get("start", 0))
    if not durations:
        return None
    return max(durations.items(), key=lambda kv: kv[1])[0]


def ensure_therapist(cur, name: str) -> str:
    cur.execute("SELECT id FROM therapists WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO therapists (name) VALUES (%s) RETURNING id", (name,))
    return cur.fetchone()[0]


def ingest_one(cur, raw_path: Path, therapist_id: str) -> tuple[str, str]:
    """Возвращает (status, raw_id_or_msg) — status: 'ingested' | 'skipped'."""
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    metadata_path = raw_path.parent / "metadata.json"
    metadata = (
        json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata_path.exists()
        else {}
    )

    file_hash = file_sha256(raw_path)
    cur.execute(
        "SELECT id FROM raw_transcripts WHERE source_file = %s AND source_hash = %s",
        (str(raw_path), file_hash),
    )
    if cur.fetchone():
        return ("skipped", str(raw_path))

    segments = raw.get("segments", [])
    metadata["therapist_speaker_id"] = dominant_speaker(segments)
    metadata["segment_count"] = metadata.get("segment_count", len(segments))

    cur.execute(
        """
        INSERT INTO raw_transcripts
          (therapist_id, source_type, source_file, source_hash, content, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            therapist_id,
            "lecture",
            str(raw_path),
            file_hash,
            json.dumps(raw, ensure_ascii=False),
            json.dumps(metadata, ensure_ascii=False),
        ),
    )
    return ("ingested", cur.fetchone()[0])


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--therapist-name",
        default="default",
        help="Имя психолога (multi-therapist ready). Создаётся если нет.",
    )
    parser.add_argument(
        "--source-type",
        default="lecture",
        choices=["lecture", "session", "bot_voice"],
        help="Тип источника (по умолчанию lecture).",
    )
    args = parser.parse_args()

    raw_files = sorted(Path("data/transcripts").glob("*/raw.json"))
    if not raw_files:
        print("Не нашёл data/transcripts/*/raw.json", file=sys.stderr)
        return 1

    print(f"Найдено raw.json: {len(raw_files)}")

    ingested = 0
    skipped = 0
    with connect() as conn, conn.cursor() as cur:
        therapist_id = ensure_therapist(cur, args.therapist_name)
        for raw in raw_files:
            status, info = ingest_one(cur, raw, therapist_id)
            if status == "ingested":
                ingested += 1
                print(f"  [+] {raw.parent.name}: id={info}")
            else:
                skipped += 1
                print(f"  [=] {raw.parent.name}: уже в БД")
        conn.commit()

    print(f"\nИтого: ingested={ingested}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
