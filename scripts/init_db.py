"""Применить миграции БД из db/migrations/.

Запуск:
    docker compose run --rm app python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from psy_helper.db.connection import connect


def main() -> int:
    load_dotenv()
    migrations_dir = Path("db/migrations")
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        print("Миграций не найдено", file=sys.stderr)
        return 1

    with connect() as conn, conn.cursor() as cur:
        for f in files:
            print(f"Применяю {f.name}...")
            cur.execute(f.read_text(encoding="utf-8"))
        conn.commit()

    print("Готово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
