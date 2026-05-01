"""Эмбеддинги для concepts через intfloat/multilingual-e5-large.

Эмбеддит "passage: <name>. <description>", чтобы запросы вида
"query: <тема>" находили концепты.

Идемпотентно: пропускает concepts с уже посчитанным embedding.

Запуск:
    docker compose run --rm app python scripts/embed_concepts.py
"""
from __future__ import annotations

import sys
import time

from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from psy_helper.db.connection import connect

MODEL_NAME = "intfloat/multilingual-e5-large"


def main() -> int:
    load_dotenv()

    with connect() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, description
                FROM concepts
                WHERE embedding IS NULL
                ORDER BY id
                """
            )
            rows = cur.fetchall()

        if not rows:
            print("Нечего эмбеддить — все концепты уже посчитаны.")
            return 0

        print(f"Концептов к обработке: {len(rows)}")
        print(f"Загрузка модели {MODEL_NAME}...")
        t0 = time.monotonic()
        model = SentenceTransformer(MODEL_NAME)
        print(f"Модель загружена за {time.monotonic() - t0:.1f}с")

        ids = [r[0] for r in rows]
        texts = [
            f"passage: {r[1]}. {(r[2] or '').strip()}".strip(". ").strip()
            for r in rows
        ]

        t0 = time.monotonic()
        embeddings = model.encode(
            texts,
            batch_size=16,
            show_progress_bar=True,
            normalize_embeddings=True,
        )
        print(f"Энкодинг {len(texts)} концептов за {time.monotonic() - t0:.1f}с")

        with conn.cursor() as cur:
            for cid, emb in zip(ids, embeddings):
                cur.execute(
                    "UPDATE concepts SET embedding = %s WHERE id = %s",
                    (emb, cid),
                )
        conn.commit()
        print(f"Записано {len(rows)} эмбеддингов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
