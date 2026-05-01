"""Эмбеддинги для clean_segments через intfloat/multilingual-e5-large.

E5-семейство требует префиксы: "passage: <text>" для документов и
"query: <text>" для запросов — это часть тренировочной конвенции.
1024-dim, мультиязычная, работает с русским.

Идемпотентно: пропускает segment_id, у которых уже есть embedding.

Запуск:
    docker compose run --rm app python scripts/embed_segments.py
"""
from __future__ import annotations

import sys
import time

from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from psy_helper.db.connection import connect

MODEL_NAME = "intfloat/multilingual-e5-large"
PASSAGE_PREFIX = "passage: "


def main() -> int:
    load_dotenv()

    with connect() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cs.id, cs.text
                FROM clean_segments cs
                LEFT JOIN segment_embeddings se ON se.segment_id = cs.id
                WHERE se.segment_id IS NULL
                ORDER BY cs.id
                """
            )
            rows = cur.fetchall()

        if not rows:
            print("Нечего эмбеддить — все сегменты уже посчитаны.")
            return 0

        print(f"Сегментов к обработке: {len(rows)}")
        print(f"Загрузка модели {MODEL_NAME} (на первом запуске качается ~2.2 ГБ)...")
        t0 = time.monotonic()
        model = SentenceTransformer(MODEL_NAME)
        print(f"Модель загружена за {time.monotonic() - t0:.1f}с")

        ids = [r[0] for r in rows]
        texts = [PASSAGE_PREFIX + (r[1] or "") for r in rows]

        t0 = time.monotonic()
        embeddings = model.encode(
            texts,
            batch_size=8,
            show_progress_bar=True,
            normalize_embeddings=True,
        )
        print(f"Энкодинг {len(texts)} сегментов за {time.monotonic() - t0:.1f}с")

        with conn.cursor() as cur:
            for seg_id, emb in zip(ids, embeddings):
                cur.execute(
                    "INSERT INTO segment_embeddings (segment_id, embedding) VALUES (%s, %s)",
                    (seg_id, emb),
                )
        conn.commit()
        print(f"Записано {len(rows)} эмбеддингов в segment_embeddings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
