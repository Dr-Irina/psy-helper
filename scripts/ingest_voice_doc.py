"""Загрузить voice-document из data/voice_document/*.md в таблицу voice_document.

Версионирование автоматическое: новая запись = max(version) + 1, прошлая
версия деактивируется (is_active=false), новая становится активной.

Запуск:
    docker compose run --rm app python scripts/ingest_voice_doc.py path/to/file.md
    docker compose run --rm app python scripts/ingest_voice_doc.py \\
        path/to/file.md --therapist "Анна" --summary "v1: первый черновик из 2 лекций"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from psy_helper.db.connection import connect


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("md_path", type=Path)
    parser.add_argument("--therapist", default="Анна")
    parser.add_argument("--summary", default="", help="Описание изменений (changes_summary)")
    parser.add_argument(
        "--no-activate",
        action="store_true",
        help="Не делать новую версию активной (по умолчанию активирует)",
    )
    args = parser.parse_args()

    if not args.md_path.exists():
        print(f"Файл не найден: {args.md_path}", file=sys.stderr)
        return 1

    content = args.md_path.read_text(encoding="utf-8")

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM therapists WHERE name = %s", (args.therapist,))
        row = cur.fetchone()
        if not row:
            print(f"Терапевт '{args.therapist}' не найден в таблице therapists", file=sys.stderr)
            return 2
        therapist_id = row[0]

        cur.execute(
            "SELECT COALESCE(MAX(version), 0) FROM voice_document WHERE therapist_id = %s",
            (therapist_id,),
        )
        next_version = cur.fetchone()[0] + 1

        if not args.no_activate:
            cur.execute(
                "UPDATE voice_document SET is_active = FALSE WHERE therapist_id = %s",
                (therapist_id,),
            )

        cur.execute(
            """
            INSERT INTO voice_document (therapist_id, version, content, changes_summary, is_active)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                therapist_id,
                next_version,
                content,
                args.summary or None,
                not args.no_activate,
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()

    print(f"Создана версия {next_version} (id={new_id}), активна={not args.no_activate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
